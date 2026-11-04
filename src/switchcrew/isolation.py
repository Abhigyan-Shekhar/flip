"""Operator-pinned Docker isolation for disposable QA/review/scanner jobs.

Never accepts a container command or options from an untrusted model response.
The host must separately ensure its Docker daemon and image are trusted.
"""

from __future__ import annotations

import math
import os
import re
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

    @classmethod
    def from_config(cls, raw):
        if (
            not isinstance(raw, dict)
            or not raw
            or set(raw) - {"image", "memory_mb", "pids_limit", "cpus", "binary"}
        ):
            raise ValueError("Invalid Docker isolation configuration")
        return cls(**raw)

    def command(self, argv, workspace: Path, *, writable: bool = False) -> list[str]:
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
        uid = os.getuid() or 65534
        gid = os.getgid() or 65534
        mount = f"--mount=type=bind,src={workspace},dst=/workspace"
        if not writable:
            mount += ",readonly"
        return [
            self.binary,
            "run",
            "--rm",
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
