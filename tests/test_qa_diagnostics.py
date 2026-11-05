"""Fault injection around real Git checkouts and durable QA retry boundaries."""

import errno
import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from switchcrew.engineering import Ledger, assignment_id, git
from switchcrew.qa import QAExecutor, failure_detail


@pytest.fixture
def validation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "fixture",
    )
    revision = git(source, "rev-parse", "HEAD")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.reserve("implementation", {})
    ledger.put(
        "implementation",
        {
            "result": {
                "status": "implemented",
                "checkout": str(source),
                "final_revision": revision,
            }
        },
    )
    executor = QAExecutor(
        ledger, tmp_path / "qa", "implementation", revision, [[sys.executable, "-c", "pass"]]
    )
    state = {"handoff_id": "qa-diagnostic", "task_id": "task"}
    yield executor, state
    ledger.close()


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            OSError(errno.ENOSPC, "password=private", "/private/input"),
            {"category": "os_error", "code": "ENOSPC"},
        ),
        (
            FileNotFoundError(errno.ENOENT, "token=private"),
            {"category": "os_error", "code": "ENOENT"},
        ),
        (
            subprocess.CalledProcessError(
                128, ["git", "https://private@host"], output=b"private source", stderr=b"secret"
            ),
            {"category": "subprocess_error", "exit_code": 128},
        ),
        (ValueError("private source"), {"category": "validation_error"}),
        (OSError(999999, "private"), {"category": "os_error"}),
        (subprocess.CalledProcessError("private", ["secret"]), {"category": "subprocess_error"}),
    ],
)
def test_diagnostic_never_serializes_exception_payload(error, expected):
    assert failure_detail("checkout", error, 1) == {
        "stage": "checkout",
        "command_index": 1,
        **expected,
    }


async def test_directory_failure_is_finished_and_not_retried(validation):
    executor, state = validation
    executor.root.write_text("existing file, not a directory")
    result = await executor(state)
    assert result["status"] == "qa_failed"
    assert result["reason"] == "validation_launch_or_checkout_failed"
    assert result["failure_detail"] == {
        "stage": "prepare_run_directory",
        "category": "os_error",
        "code": "EEXIST",
    }
    executor.root.unlink()
    assert await executor(state) == result
    assert not executor.root.exists()


@pytest.mark.parametrize("stage", ["clone", "checkout", "verify_worktree"])
async def test_git_failure_is_bounded_and_durable(validation, monkeypatch, stage):
    import switchcrew.qa as qa

    executor, state = validation
    if stage == "clone":
        original_run = qa.subprocess.run

        def fail(args, **kwargs):
            if args[:2] == ["git", "clone"]:
                raise OSError(errno.ENOSPC, "SENSITIVE_EXCEPTION_SENTINEL", "/hidden/input")
            return original_run(args, **kwargs)

        monkeypatch.setattr(qa.subprocess, "run", fail)
    else:
        original = qa.git

        def fail(checkout, *args):
            if (stage == "checkout" and args[0] == "checkout") or (
                stage == "verify_worktree" and args[0] == "diff"
            ):
                raise subprocess.CalledProcessError(
                    128, ["secret"], stderr=b"SENSITIVE_EXCEPTION_SENTINEL"
                )
            return original(checkout, *args)

        monkeypatch.setattr(qa, "git", fail)
    result = await executor(state)
    assert result["status"] == "qa_failed"
    assert result["tested_revision"] == executor.revision
    assert result["failure_detail"] == {
        "stage": stage,
        "command_index": 0,
        **(
            {"category": "os_error", "code": "ENOSPC"}
            if stage == "clone"
            else {"category": "subprocess_error", "exit_code": 128}
        ),
    }
    assert "SENSITIVE_EXCEPTION_SENTINEL" not in json.dumps(result)
    assert "secret" not in json.dumps(result)
    assert len(result["commands"]) == (1 if stage == "verify_worktree" else 0)
    assert await executor(state) == result


async def test_launch_failure_retains_first_command_and_new_attempt_keeps_old_failure(validation):
    executor, state = validation
    executor.commands.append(["/nonexistent/qa-validation-binary"])
    failed = await executor(state)
    assert failed["status"] == "qa_failed"
    assert failed["failure_detail"] == {
        "stage": "command_execution",
        "command_index": 1,
        "category": "os_error",
        "code": "ENOENT",
    }
    assert len(failed["commands"]) == 1
    assert failed["commands"][0]["exit_code"] == 0
    assert failed["commands"][0]["revision"] == executor.revision
    # A fresh ledger handle models a restart; the failed attempt is immutable.
    restarted = Ledger(executor.root.parent / "ledger.sqlite3")
    try:
        executor.ledger = restarted
        assert await executor(state) == failed
        executor.commands.pop()
        changed = await executor(state)
        assert changed["reason"] == "qa_assignment_inputs_changed"
        fresh = await executor({**state, "handoff_id": "new-operator-reconciled-attempt"})
        assert fresh["status"] == "verified_success"
        assert restarted.get("qa-" + assignment_id(state["handoff_id"]))["result"] == failed
    finally:
        restarted.close()


@dataclass
class ControlledIsolation:
    fault: str

    @contextmanager
    def launch(self, argv, checkout, *, writable):
        if self.fault == "setup":
            raise OSError(errno.EIO, "private daemon error")
        yield argv
        raise OSError(errno.EIO, "private cleanup error")


@pytest.mark.parametrize("fault", ["setup", "cleanup"])
async def test_isolation_errors_fail_closed_with_stage(validation, fault):
    executor, state = validation
    executor.isolation = ControlledIsolation(fault)
    result = await executor(state)
    assert result["status"] == "qa_failed"
    assert result["failure_detail"] == {
        "stage": "isolation_" + fault,
        "command_index": 0,
        "category": "os_error",
        "code": "EIO",
    }
    assert len(result["commands"]) == (1 if fault == "cleanup" else 0)
    if result["commands"]:
        assert result["commands"][0]["exit_code"] == 0
        assert result["commands"][0]["revision"] == executor.revision
    assert await executor(state) == result


@pytest.mark.parametrize(
    "detail, expected",
    [
        ({"stage": "clone", "category": "os_error", "code": "ENOSPC"}, "ENOSPC"),
        ({"stage": "checkout", "category": "subprocess_error", "exit_code": 128}, "exit 128"),
        ({"stage": "clone", "category": "validation_error", "code": "SECRET"}, "validation error"),
        ({"stage": "SECRET", "category": "os_error"}, "could not finish its checks"),
        ({"stage": ["SECRET"], "category": {}}, "could not finish its checks"),
        ("SECRET", "could not finish its checks"),
    ],
)
def test_discord_diagnostic_rejects_free_text_fields(detail, expected):
    from switchcrew.wire import render_message

    result = {
        "status": "qa_failed",
        "reason": "validation_launch_or_checkout_failed",
        "failure_detail": detail,
        "stderr": "SECRET",
        "commands": [],
    }
    text = render_message(
        {
            "id": "receipt",
            "payload": {
                "type": "handoff.completed",
                "result": result,
            },
        },
        {},
        {},
        role="qa",
    )
    assert expected in text
    assert "SECRET" not in text
    assert "Release remains blocked" in text
    assert "tests failed" not in text
