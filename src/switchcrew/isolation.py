"""Operator-pinned Docker isolation for disposable QA/review/scanner jobs.

Never accepts a container command or options from an untrusted model response.
The host must separately ensure its Docker daemon and image are trusted.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]*@sha256:[a-f0-9]{64}$")


@dataclass(frozen=True)
class DockerIsolation:
    image: str
    memory_mb: int = 768
    pids_limit: int = 128
    cpus: float = 1.0
    binary: str = "docker"
    daemon_socket: str | None = None
    rootless: bool = False

    def __post_init__(self):
        if not isinstance(self.image, str) or not _IMAGE.fullmatch(self.image):
            raise ValueError("Docker image must be pinned by a full sha256 digest")
        if type(self.memory_mb) is not int or not 128 <= self.memory_mb <= 4096:
            raise ValueError("Docker memory must be between 128 and 4096 MiB")
        if type(self.pids_limit) is not int or not 16 <= self.pids_limit <= 512:
            raise ValueError("Docker PID limit must be between 16 and 512")
        if (
            isinstance(self.cpus, bool)
            or not isinstance(self.cpus, (int, float))
            or not math.isfinite(self.cpus)
            or not 0.25 <= self.cpus <= 4.0
        ):
            raise ValueError("Docker CPU quota must be bounded")
        if self.binary != "docker" and not (
            Path(self.binary).is_absolute() and Path(self.binary).is_file()
        ):
            raise ValueError("Docker binary must be docker or an existing absolute executable")
        if self.daemon_socket is not None and (
            not isinstance(self.daemon_socket, str)
            or not Path(self.daemon_socket).is_absolute()
            or any(char in self.daemon_socket for char in "\x00\r\n")
        ):
            raise ValueError("Docker daemon socket must be an absolute Unix socket path")
        if type(self.rootless) is not bool or (self.rootless and not self.daemon_socket):
            raise ValueError("Rootless Docker requires an explicit daemon socket")

    @classmethod
    def from_config(cls, raw):
        allowed = {
            "image",
            "memory_mb",
            "pids_limit",
            "cpus",
            "binary",
            "daemon_socket",
            "rootless",
        }
        if not isinstance(raw, dict) or not raw or set(raw) - allowed:
            raise ValueError("Invalid Docker isolation configuration")
        return cls(**raw)

    def _docker(self, *args: str) -> list[str]:
        socket = [f"--host=unix://{self.daemon_socket}"] if self.daemon_socket else []
        return [self.binary, *socket, *args]

    def _invoke(self, *args: str) -> str:
        try:
            result = subprocess.run(
                self._docker(*args),
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": os.defpath, "HOME": "/nonexistent", "LANG": "C.UTF-8"},
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError("Docker daemon cleanup or validation failed") from exc

    def _verify_rootless(self) -> None:
        if self.rootless:
            options = json.loads(self._invoke("info", "--format", "{{json .SecurityOptions}}"))
            if not isinstance(options, list) or "name=rootless" not in options:
                raise OSError("Configured Docker daemon is not rootless")

    def _cleanup(self, name: str) -> None:
        args = ("ps", "--all", "--quiet", "--no-trunc", "--filter", f"name=^/{name}$")
        if self._invoke(*args):
            self._invoke("rm", "--force", name)
            if self._invoke(*args):
                raise OSError("Docker container survived enforced cleanup")

    @contextmanager
    def launch(self, argv, workspace: Path, *, writable: bool = False):
        """Always reap the named container, including on timeout or cancellation."""
        self._verify_rootless()
        name = f"switchcrew-{uuid.uuid4().hex}"
        try:
            yield self.command(argv, workspace, writable=writable, name=name)
        finally:
            self._cleanup(name)

    def command(
        self, argv, workspace: Path, *, writable: bool = False, name: str | None = None
    ) -> list[str]:
        if (
            not isinstance(argv, (list, tuple))
            or not argv
            or any(not isinstance(a, str) or not a or "\x00" in a for a in argv)
        ):
            raise ValueError("Operator argv must be a bounded nonempty argument list")
        workspace = Path(workspace).resolve(strict=True)
        if not workspace.is_dir() or any(c in str(workspace) for c in ",\r\n"):
            raise ValueError("Docker workspace must be a safe existing directory")
        if len(argv) > 100 or sum(map(len, argv)) > 16000:
            raise ValueError("Docker argv exceeds operator command limit")
        if name is not None and not re.fullmatch(r"switchcrew-[0-9a-f]{32}", name):
            raise ValueError("Docker container name is invalid")
        # Rootless container UID 0 maps to the unprivileged host worker's UID.
        uid = 0 if self.rootless else (os.getuid() or 65534)
        gid = 0 if self.rootless else (os.getgid() or 65534)
        mount = f"--mount=type=bind,src={workspace},dst=/workspace"
        if not writable:
            mount += ",readonly"
        return [
            *self._docker("run"),
            "--rm",
            *([f"--name={name}"] if name else []),
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--ipc=private",
            f"--pids-limit={self.pids_limit}",
            f"--memory={self.memory_mb}m",
            f"--cpus={self.cpus}",
            f"--user={uid}:{gid}",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
            mount,
            "--workdir=/workspace",
            "--env=HOME=/tmp",
            "--env=TMPDIR=/tmp",
            f"--entrypoint={argv[0]}",
            self.image,
            *argv[1:],
        ]
