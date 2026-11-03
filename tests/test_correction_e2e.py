"""Real Git/check commands and controlled Switch/Console/GitHub; no live-service claim."""

import json
import sys

import pytest
from test_approval_release import GitHubDouble, handoff_state
from test_engineering_qa import ControlledSession, commit, receipt
from test_workers import Transport, event

from switchcrew import engineering_qa
from switchcrew.approval import ApprovalWorkflow
from switchcrew.engineering import Ledger, assignment_id, git
from switchcrew.graphs import build_agent_graph
from switchcrew.release import ReleaseExecutor
from switchcrew.runtime import AgentConnection, AgentRuntime
from switchcrew.store import ConflictError, Store


@pytest.mark.parametrize("rejector", ["qa", "reviewer"])
async def test_switch_correction_full_loop_fresh_sha_and_receipt(tmp_path, monkeypatch, rejector):
    store, ledger = Store(tmp_path / "app.db"), Ledger(tmp_path / "ledger.db")
    workflow = ApprovalWorkflow(store, ["owner"])
    task = store.create_task("Correct answer", {"room_id": "room"}, ["Answer is correct-reviewed"])
    workflow.request(
        task["id"],
        "pm",
        "engineering",
        "qa",
        "release",
        "fix",
        "org/repo",
        "master",
        reviewer="reviewer",
        max_corrections=2,
    )
    data = workflow.decide(task["id"], "engineering", True, "owner", 1)
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    original_base = commit(source, "broken")
    session = ControlledSession()
    monkeypatch.setattr(engineering_qa, "ConsoleCodex", lambda *a, **kw: session)
    checks = [
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "assert Path('answer.txt').read_text().startswith('correct')"
            ),
        ]
    ]
    reviews = [
        [
            sys.executable,
            "-c",
            (
                "import json, subprocess; from pathlib import Path; "
                "patch = subprocess.check_output(['git','diff','{base_revision}','{revision}']); "
                "assert b'answer.txt' in patch; "
                "ok = Path('answer.txt').read_text() == 'correct-reviewed'; "
                "print(json.dumps({'accepted':ok,'findings':[] if ok else "
                "[{'path':'answer.txt','line':1,"
                "'message':'Preserve the reviewed answer suffix'}]}))"
            ),
        ]
    ]
    runtimes = {}
    for role in ("engineering", "qa", "reviewer"):
        connection = AgentConnection(
            role,
            role,
            "participant-" + role,
            role,
            "private",
            "room",
            allowed_senders={
                "participant-" + peer: peer
                for peer in ("pm", "engineering", "qa", "reviewer")
                if peer != role
            },
            target_names={peer: peer for peer in ("pm", "engineering", "qa", "reviewer")},
        )
        config = {
            "checkout_root": str(tmp_path / role),
            "control_file": "unused",
            "console_database": "unused",
            "job_templates": {
                "fix": {
                    "source": str(source),
                    "base_revision": original_base,
                    "console_agent_id": "codex",
                    "codex_name": "codex",
                    "codex_switch_agent_id": "codex",
                    "commands": reviews if role == "reviewer" else checks,
                }
            },
        }
        executor = engineering_qa.configured_executor(
            config, connection, Transport(), store, ledger, workflow
        )
        transport = Transport()
        runtimes[role] = AgentRuntime(
            connection, store, transport, build_agent_graph(role, executor=executor)
        )

    async def deliver(identity, recipient):
        handoff = store.get_handoff(identity)
        # Use the persisted transport envelope, with the sender's independent identity.
        outgoing = next(o for o in store.pending_outbox() if o["idempotency_key"] == identity)
        payload = {**outgoing["payload"], "task_id": task["id"], "delivery_id": outgoing["id"]}
        envelope = event("participant-" + handoff["sender"], json.dumps(payload), outgoing["id"])
        assert await runtimes[recipient].receive(envelope)
        assert not await runtimes[recipient].receive(envelope)
        await runtimes[recipient].process_pending()
        return handoff

    first_engineering = data["engineering_handoff"]
    await deliver(first_engineering, "engineering")
    first_sha = commit(session.checkout, "wrong" if rejector == "qa" else "correct")
    receipt(session.checkout, assignment_id(first_engineering), first_sha)
    await runtimes["engineering"].process_pending()
    data = workflow.reconcile(task["id"])
    first_qa = data["qa_handoff"]
    await deliver(first_qa, "qa")
    data = workflow.reconcile(task["id"])
    if rejector == "reviewer":
        await deliver(data["review_handoff"], "reviewer")
        data = workflow.reconcile(task["id"])
    assert data["phase"] == "engineering" and data["attempt"] == 1
    corrected_handoff = store.get_handoff(data["engineering_handoff"])
    assert corrected_handoff["sender"] == rejector
    assert corrected_handoff["acceptance_criteria"] == task["acceptance_criteria"]
    assert workflow.get(task["id"])["engineering_approval"]["actor"] == "owner"
    # A fresh coordinator must not schedule a second attempt for the same rejection.
    restarted = ApprovalWorkflow(store, ["owner"])
    assert restarted.reconcile(task["id"])["engineering_handoff"] == corrected_handoff["id"]
    await deliver(corrected_handoff["id"], "engineering")
    assert git(session.checkout, "rev-parse", "HEAD") == first_sha
    final_sha = commit(session.checkout, "correct-reviewed")
    receipt(session.checkout, assignment_id(corrected_handoff["id"]), final_sha)
    await runtimes["engineering"].process_pending()
    data = workflow.reconcile(task["id"])
    assert data["qa_handoff"] != first_qa and data["revision"] == final_sha
    implementation = ledger.get(assignment_id(corrected_handoff["id"]))["result"]
    assert implementation["base_revision"] == original_base
    assert implementation["checkout_base_revision"] == first_sha
    await deliver(data["qa_handoff"], "qa")
    data = workflow.reconcile(task["id"])
    assert data["phase"] == "review"
    with pytest.raises(ConflictError):
        workflow.decide(task["id"], "release", True, "owner", store.get_task(task["id"])["version"])
    await deliver(data["review_handoff"], "reviewer")
    data = workflow.reconcile(task["id"])
    assert data["phase"] == "human_review"
    data = workflow.decide(
        task["id"], "release", True, "owner", store.get_task(task["id"])["version"]
    )
    publisher = GitHubDouble()
    release = ReleaseExecutor(workflow, ledger, "release", publisher)
    result = await release(handoff_state(store, data["release_handoff"], "release"))
    assert result["status"] == "completed"
    record = result["receipt"]
    assert record["commit_sha"] == record["review"]["reviewed_revision"] == final_sha
    assert all(c["revision"] == final_sha for c in record["tests"])
    assert record["retry_history"][0]["revision"] == first_sha
    assert record["files_changed"] == ["answer.txt"]
    assert record["assigned_agents"]["reviewer"] == "reviewer"
    assert len(publisher.posts) == 1
    store.close()
    ledger.close()


def test_process_death_during_correction_transaction_rolls_back_and_recovers_once(tmp_path):
    import os
    import signal
    import subprocess
    from pathlib import Path

    from test_review_workflow import complete_qa, rejected_review, setup

    store, workflow, task_id, data = setup(tmp_path)
    complete_qa(store, data)
    data = workflow.reconcile(task_id)
    rejected_review(store, data)
    store.close()
    code = """
import os, signal, sys
from switchcrew.store import Store
from switchcrew.approval import ApprovalWorkflow
s = Store(sys.argv[1])
w = ApprovalWorkflow(s)
create = s.create_handoff
def interrupted(*args, **kwargs):
    result = create(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGKILL)
s.create_handoff = interrupted
w.reconcile(sys.argv[2])
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "approval.db"), task_id],
        env={"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        timeout=10,
    )
    assert process.returncode == -signal.SIGKILL
    recovered = Store(tmp_path / "approval.db")
    flow = ApprovalWorkflow(recovered)
    assert flow.get(task_id)["attempt"] == 0
    assert flow.get(task_id)["phase"] == "review"
    next_attempt = flow.reconcile(task_id)
    assert next_attempt["attempt"] == 1
    assert flow.reconcile(task_id)["engineering_handoff"] == next_attempt["engineering_handoff"]
    assert (
        recovered.connection.execute(
            "SELECT count(*) FROM handoffs WHERE task_id=? AND sender='reviewer'", (task_id,)
        ).fetchone()[0]
        == 1
    )
    recovered.close()
