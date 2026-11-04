import json

import pytest

from switchcrew.isolation import DockerIsolation
from switchcrew.organization_runtime import configured_executor
from switchcrew.security import ScannerConfig, SecurityRunner

PIN = "flip-demo-scanner@sha256:" + "a" * 64


def test_container_policy_requires_digest_and_bounded_resources():
    with pytest.raises(ValueError, match="pinned"):
        DockerIsolation("ubuntu:latest")
    with pytest.raises(ValueError, match="memory"):
        DockerIsolation(PIN, memory_mb=100_000)
    with pytest.raises(ValueError, match="CPU"):
        DockerIsolation(PIN, cpus=float("nan"))
    with pytest.raises(ValueError, match="configuration"):
        DockerIsolation.from_config({"image": PIN, "privileged": True})


def test_container_command_enforces_readonly_networkless_rootfs(tmp_path):
    policy = DockerIsolation(PIN)
    argv = policy.command(["python", "-m", "pytest"], tmp_path)
    assert argv[:3] == ["docker", "run", "--rm"]
    for rule in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pull=never",
        "--ipc=private",
    ):
        assert rule in argv
    assert any(a.startswith("--mount=") and a.endswith("readonly") for a in argv)
    assert "--entrypoint=python" in argv
    assert argv[-3:] == [PIN, "-m", "pytest"]
    writable = policy.command(["python"], tmp_path, writable=True)
    mount = next(v for v in writable if v.startswith("--mount="))
    assert "readonly" not in mount
    assert "--network=none" in writable
    with pytest.raises(ValueError, match="Operator"):
        policy.command([], tmp_path)


@pytest.mark.asyncio
async def test_scanner_runs_via_container_command_without_host_scanner(tmp_path):
    marker = tmp_path / "invocation.json"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'findings': []}))\n"
    )
    fake_docker.chmod(0o700)
    (tmp_path / "checkout").mkdir()
    runner = SecurityRunner(
        [ScannerConfig("configured", ("/scanner", "--input", "${repo_root}"))],
        isolation=DockerIsolation(PIN, binary=str(fake_docker)),
    )
    status, output, warning = await runner._execute(
        ["/scanner", "--input", "/workspace"], tmp_path / "checkout", str(tmp_path), 10, (0,)
    )
    assert status == "completed" and not warning
    assert json.loads(output)["findings"] == []
    argv = json.loads(marker.read_text())
    assert "--network=none" in argv
    assert "--entrypoint=/scanner" in argv
    assert argv[-2:] == ["--input", "/workspace"]


def test_strict_security_gate_rejects_unisolated_configuration(tmp_path):
    class Stub:
        role = "security"
        agent_id = "security"

    class SettingsStub:
        pass

    with pytest.raises(ValueError, match="Isolated security scanner"):
        configured_executor(
            "security",
            {"mode": "engineering_security_gate", "require_isolation": True},
            None,
            Stub(),
            SettingsStub(),
        )
