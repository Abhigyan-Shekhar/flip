import json

import pytest

from switchcrew.engineering import Ledger, assignment_id
from switchcrew.isolation import DockerIsolation
from switchcrew.organization_runtime import configured_executor
from switchcrew.qa import QAExecutor
from switchcrew.reviewer import ReviewerExecutor
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
        "args = sys.argv[1:]\n"
        "if 'run' in args:\n"
        f"    pathlib.Path({str(marker)!r}).write_text(json.dumps(args))\n"
        "    print(json.dumps({'findings': []}))\n"
        "elif 'ps' in args:\n"
        "    print('')\n"
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


def test_rootless_config_uses_explicit_socket_and_namespace_uid(tmp_path):
    with pytest.raises(ValueError, match="Rootless"):
        DockerIsolation(PIN, rootless=True)
    policy = DockerIsolation(PIN, daemon_socket="/run/user/1000/docker.sock", rootless=True)
    argv = policy.command(["python"], tmp_path, writable=True)
    assert argv[:3] == ["docker", "--host=unix:///run/user/1000/docker.sock", "run"]
    assert "--user=0:0" in argv
    assert "readonly" not in next(x for x in argv if x.startswith("--mount="))


def test_container_is_reaped_when_worker_times_out(tmp_path):
    marker = tmp_path / "still-running"
    fake_docker = tmp_path / "fake-docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "if 'ps' in args and marker.exists(): print('c' * 64)\n"
        "if 'rm' in args: marker.unlink(missing_ok=True)\n"
    )
    fake_docker.chmod(0o700)
    policy = DockerIsolation(PIN, binary=str(fake_docker))
    with pytest.raises(TimeoutError):
        with policy.launch(["python"], tmp_path) as command:
            assert any(x.startswith("--name=switchcrew-") for x in command)
            marker.write_text("running")
            raise TimeoutError("worker timeout")
    assert not marker.exists()


def test_rootless_mode_requires_a_verified_rootless_daemon(tmp_path):
    fake_docker = tmp_path / "fake-docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\nimport sys\nif 'info' in sys.argv: print('[\"name=seccomp\"]')\n"
    )
    fake_docker.chmod(0o700)
    policy = DockerIsolation(
        PIN,
        binary=str(fake_docker),
        daemon_socket="/run/user/1000/docker.sock",
        rootless=True,
    )
    with pytest.raises(OSError, match="not rootless"):
        with policy.launch(["python"], tmp_path):
            pytest.fail("unverified daemon should never execute the job")


@pytest.mark.asyncio
async def test_cached_unisolated_qa_result_cannot_satisfy_strict_policy(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "qa.db")
    handoff = "qa-retry"
    identity = "qa-" + assignment_id(handoff)
    revision = "a" * 40
    commands = [["python", "-m", "pytest"]]
    ledger.reserve(
        identity,
        {
            "implementation": "implementation",
            "revision": revision,
            "commands": commands,
            "timeout": 120,
        },
    )
    ledger.put(identity, {"phase": "finished", "result": {"status": "verified_success"}})
    executor = QAExecutor(
        ledger,
        tmp_path,
        "implementation",
        revision,
        commands,
        isolation=DockerIsolation(PIN),
    )
    monkeypatch.setattr(executor, "current", lambda: {"status": "implemented"})
    monkeypatch.setattr(executor, "stale", lambda: False)
    result = await executor({"handoff_id": handoff})
    assert result["status"] == "blocked_configuration"
    assert result["reason"] == "qa_assignment_inputs_changed"
    ledger.close()


@pytest.mark.asyncio
async def test_cached_unisolated_review_cannot_satisfy_strict_policy(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "review.db")
    handoff = "review-retry"
    identity = "review-" + assignment_id(handoff)
    revision, base = "a" * 40, "b" * 40
    commands = [["review", "--json"]]
    ledger.reserve(
        identity,
        {
            "implementation": "implementation",
            "revision": revision,
            "base": base,
            "commands": commands,
            "timeout": 120,
        },
    )
    ledger.put(identity, {"phase": "finished", "result": {"status": "review_accepted"}})
    executor = ReviewerExecutor(
        ledger, tmp_path, "implementation", revision, commands, isolation=DockerIsolation(PIN)
    )
    monkeypatch.setattr(
        executor, "_current", lambda: ({"status": "implemented"}, tmp_path, base, b"diff", [])
    )
    result = await executor({"handoff_id": handoff})
    assert result["status"] == "blocked_configuration"
    assert result["reason"] == "review_assignment_inputs_changed"
    ledger.close()
