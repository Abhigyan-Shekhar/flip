import hashlib
import hmac
import json
import subprocess
import sys

import pytest

from switchcrew.security import ScannerConfig, SecurityRunner
from switchcrew.store import Store
from switchcrew.triggers import (
    TriggeredQAExecutor,
    TriggeredSecurityExecutor,
    WebhookRejected,
    accept_github_webhook,
)


def git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    (path / "check.txt").write_text("pinned content\n")
    subprocess.run(["git", "-C", str(path), "add", "check.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    return path.resolve(), subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


TEST_SECRET = b"webhook test secret at least 32 bytes"


def signed_request(payload, *, event="push", delivery="delivery-1", secret=TEST_SECRET):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": f"sha256={signature}",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
    }
    return headers, body


def push_payload(revision, repo="owner/project"):
    return {"repository": {"full_name": repo}, "ref": "refs/heads/main", "after": revision}


def accept(store, headers, body, repo="owner/project"):
    return accept_github_webhook(
        store,
        headers,
        body,
        secret=TEST_SECRET,
        repository=repo,
        qa_recipient="qa-agent",
        security_recipient="security-agent",
    )


def test_signature_scope_and_event_are_validated_before_writes(tmp_path):
    _, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    bad = dict(headers, **{"X-Hub-Signature-256": "sha256=" + "0" * 64})
    with pytest.raises(WebhookRejected, match="signature_invalid"):
        accept(store, bad, body)
    with pytest.raises(WebhookRejected, match="repository_scope_mismatch"):
        accept(store, headers, body, repo="other/project")
    invalid_sha_headers, invalid_sha_body = signed_request(push_payload("f" * 39))
    with pytest.raises(WebhookRejected, match="revision_missing"):
        accept(store, invalid_sha_headers, invalid_sha_body)
    pr_headers, pr_body = signed_request(
        {
            "repository": {"full_name": "owner/project"},
            "action": "closed",
            "pull_request": {"head": {"sha": revision}},
        },
        event="pull_request",
    )
    with pytest.raises(WebhookRejected, match="action_not_authorized"):
        accept(store, pr_headers, pr_body)
    assert store.connection.execute("select count(*) from tasks").fetchone()[0] == 0


def test_signed_delivery_creates_durable_qa_security_outbox_and_deduplicates_restart(tmp_path):
    _, revision = git_repo(tmp_path / "repo")
    path = tmp_path / "store.sqlite"
    store = Store(path)
    headers, body = signed_request(push_payload(revision))
    first = accept(store, headers, body)
    assert first["status"] == "accepted"
    assert first["outbox_pending"] is True
    task = store.get_task(first["task_id"])
    assert task["status"] == "IN_PROGRESS"
    assert task["source"]["revision"] == revision
    qa = store.get_handoff(first["qa_handoff_id"])
    security = store.get_handoff(first["security_handoff_id"])
    assert qa["recipient"] == "qa-agent"
    assert security["recipient"] == "security-agent"
    assert qa["sender"] == "pm"
    assert (
        store.connection.execute(
            "select count(*) from outbox where status='pending' "
            "and recipient in ('qa-agent', 'security-agent')"
        ).fetchone()[0]
        == 2
    )
    store.close()

    reopened = Store(path)
    second = accept(reopened, headers, body)
    assert second["status"] == "duplicate"
    assert second["task_id"] == first["task_id"]
    assert reopened.connection.execute("select count(*) from tasks").fetchone()[0] == 1
    assert reopened.connection.execute("select count(*) from outbox").fetchone()[0] == 4
    assert (
        reopened.connection.execute(
            "select count(*) from outbox where recipient in ('qa-agent', 'security-agent')"
        ).fetchone()[0]
        == 2
    )
    third_headers, third_body = signed_request(
        push_payload(revision), delivery="different-delivery"
    )
    third = accept(reopened, third_headers, third_body)
    assert third["status"] == "duplicate"
    assert third["task_id"] == first["task_id"]
    assert reopened.connection.execute("select count(*) from tasks").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_triggered_qa_requires_persisted_scope_and_runs_operator_argv_at_exact_sha(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    receipt = accept(store, headers, body)
    executor = TriggeredQAExecutor(
        store, root, "qa-agent", "owner/project", [["git", "rev-parse", "HEAD"]]
    )
    result = await executor({"handoff_id": receipt["qa_handoff_id"]})
    assert result["status"] == "verified_success"
    assert result["tested_revision"] == revision
    assert result["commands"][0]["output"].strip() == revision
    denied = await TriggeredQAExecutor(
        store, root, "some-other-agent", "owner/project", [["git", "rev-parse", "HEAD"]]
    )({"handoff_id": receipt["qa_handoff_id"]})
    assert denied["status"] == "blocked"


@pytest.mark.asyncio
async def test_triggered_qa_requires_operator_commands_and_times_out(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    receipt = accept(store, headers, body)
    blocked = await TriggeredQAExecutor(store, root, "qa-agent", "owner/project", [])(
        {"handoff_id": receipt["qa_handoff_id"]}
    )
    assert blocked["status"] == "blocked_configuration"
    slow = await TriggeredQAExecutor(
        store,
        root,
        "qa-agent",
        "owner/project",
        [[sys.executable, "-c", "import time; time.sleep(5)"]],
        timeout=0.5,
    )({"handoff_id": receipt["qa_handoff_id"]})
    assert slow["status"] == "qa_failed"
    assert slow["reason"] == "operator_command_timed_out"


@pytest.mark.asyncio
async def test_triggered_qa_bounds_subprocess_output(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    receipt = accept(store, headers, body)
    result = await TriggeredQAExecutor(
        store,
        root,
        "qa-agent",
        "owner/project",
        [[sys.executable, "-c", "print('x' * 2000000)"]],
        timeout=5,
    )({"handoff_id": receipt["qa_handoff_id"]})
    assert result["status"] == "qa_failed"
    assert result["commands"][0]["exit_code"] == -1
    assert len(result["commands"][0]["output"]) < 100


def test_reused_delivery_id_with_changed_body_is_rejected(tmp_path):
    _, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    accept(store, headers, body)
    newer = "a" * 40 if revision != "a" * 40 else "b" * 40
    headers2, body2 = signed_request(push_payload(newer))
    # Same stable delivery identity, different signed body.
    headers2["X-GitHub-Delivery"] = headers["X-GitHub-Delivery"]
    with pytest.raises(ValueError):
        accept(store, headers2, body2)


def test_operator_source_context_is_allowlisted_and_persisted(tmp_path):
    _, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    result = accept_github_webhook(
        store,
        headers,
        body,
        secret=TEST_SECRET,
        repository="owner/project",
        qa_recipient="qa-agent",
        security_recipient="security-agent",
        sender="pm-bot",
        source_context={"discord_channel_id": "chan-1", "discord_thread_id": "thread-2"},
        project_id="oss-project",
        source_mapping={"source_kind": "push"},
    )
    task = store.get_task(result["task_id"])
    assert task["source"]["request_context"] == {
        "discord_channel_id": "chan-1",
        "discord_thread_id": "thread-2",
    }
    assert task["source"]["project_id"] == "oss-project"
    assert task["source"]["source_kind"] == "push"
    assert task["source"]["channel_id"] == "chan-1"
    assert task["source"]["thread_id"] == "thread-2"
    assert store.get_handoff(result["qa_handoff_id"])["sender"] == "pm-bot"


@pytest.mark.asyncio
async def test_triggered_security_is_bound_to_persisted_security_assignment(tmp_path):
    from pathlib import Path

    root, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    headers, body = signed_request(push_payload(revision))
    receipt = accept(store, headers, body)
    runner = SecurityRunner(
        [
            ScannerConfig(
                "fixture",
                (sys.executable, "-c", "import json; print(json.dumps({'findings': []}))"),
            )
        ]
    )
    report = await TriggeredSecurityExecutor(
        store, root, "security-agent", "owner/project", runner
    )({"handoff_id": receipt["security_handoff_id"]})
    assert report["status"] == "verified_success"
    assert report["revision"] == revision
    denied = await TriggeredSecurityExecutor(
        store, Path(root), "another-agent", "owner/project", runner
    )({"handoff_id": receipt["security_handoff_id"]})
    assert denied["status"] == "blocked"


async def test_triggered_qa_cannot_pass_after_test_mutates_pinned_tree(tmp_path):
    root, revision = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "state.db")
    headers, body = signed_request(push_payload(revision))
    receipt = accept(store, headers, body)
    executor = TriggeredQAExecutor(
        store,
        root,
        "qa-agent",
        "owner/project",
        [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('check.txt').write_text('changed')",
            ]
        ],
    )
    result = await executor({"handoff_id": receipt["qa_handoff_id"]})
    assert result["status"] == "qa_failed"
    assert result["commands"][0]["revision_unchanged"] is False
    store.close()
