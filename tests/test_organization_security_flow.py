"""Store-backed integration coverage for organization security policy wiring."""

import sys

import pytest
from test_approval_release import implement_and_review
from test_engineering_qa import commit
from test_workers import Transport

from switchcrew.approval import ApprovalWorkflow
from switchcrew.engineering import Ledger, git
from switchcrew.organization_runtime import ChangeIntelligenceExecutor
from switchcrew.security import ScannerConfig, SecurityRunner
from switchcrew.security_gate import (
    GateSecurityExecutor,
    configure_security_policy,
)
from switchcrew.store import Store
from switchcrew.workers import Worker

PROJECT = "github.com/acme/integration"
POLICY = {"block_at_or_above": "high"}


def _new_flow(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    ledger = Ledger(tmp_path / "execution.sqlite3")
    workflow = ApprovalWorkflow(store, ["owner"])
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    base = commit(source, "broken")
    task = store.create_task(
        "Correct answer.txt",
        {"project_id": PROJECT, "room_id": "room", "thread_id": "thread"},
        ["Answer equals correct"],
    )
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
        max_corrections=0,
    )
    flow = (store, ledger, workflow, task["id"], source, base)
    return flow


def _configure(flow, tmp_path):
    store = flow[0]
    policy = {**POLICY, "allowed_repo_roots": [str(tmp_path)]}
    configure_security_policy(store, PROJECT, "pm", "security", policy)
    return {"security_id": "security", "policy": policy}


def _pm(store, gate, documentation=True):
    targets = (
        {"documentation": {"agent_id": "docs", "name": "Documentation"}} if documentation else {}
    )
    worker = Worker(
        "pm",
        store,
        Transport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        specialist_targets=targets,
        engineering_id="engineering",
        qa_id="qa",
        reviewer_id="reviewer",
        release_id="release",
        approval_users=["owner"],
    )
    worker.security_gate = gate
    return worker


@pytest.mark.asyncio
async def test_configured_gate_does_not_crash_pm_poll_before_engineering_approval(tmp_path):
    flow = _new_flow(tmp_path)
    store, _ledger, workflow, task_id, *_ = flow
    gate = _configure(flow, tmp_path)
    assert workflow.get(task_id)["phase"] == "awaiting_engineering_approval"
    await _pm(store, gate, documentation=False).process_pending()
    assert workflow.get(task_id)["phase"] == "awaiting_engineering_approval"
    assert not store.connection.execute("SELECT * FROM security_gates").fetchall()
    store.close()
    flow[1].close()


async def _human_review(tmp_path):
    flow = _new_flow(tmp_path)
    final, session, qa_result = await implement_and_review(flow, tmp_path)
    store, ledger, workflow, task_id, source, base = flow
    assert qa_result["status"] == "verified_success"
    assert workflow.get(task_id)["phase"] == "human_review"
    assert session.checkout is not None
    return flow, final


@pytest.mark.asyncio
async def test_pending_security_gate_defers_docs_then_success_queues_once(tmp_path):
    flow, _final = await _human_review(tmp_path)
    store, ledger, workflow, task_id, *_ = flow
    gate_config = _configure(flow, tmp_path)
    pm = _pm(store, gate_config)
    try:
        await pm.process_pending()
        assert not store.connection.execute(
            "SELECT id FROM tasks WHERE json_extract(source,'$.kind')='change_intelligence'"
        ).fetchall()

        gate_row = store.connection.execute(
            "SELECT * FROM security_gates WHERE task_id=?", (task_id,)
        ).fetchone()
        assert gate_row is not None
        security_handoff = store.get_handoff(gate_row["security_handoff_id"])
        store.accept_handoff(security_handoff["id"], "security")
        executor = GateSecurityExecutor(
            store,
            "security",
            SecurityRunner(
                [
                    ScannerConfig(
                        "controlled-empty",
                        (
                            sys.executable,
                            "-c",
                            "import json; print(json.dumps({'findings': []}))",
                        ),
                    )
                ]
            ),
        )
        result = await executor(
            {"task_id": security_handoff["task_id"], "handoff_id": security_handoff["id"]}
        )
        assert result["status"] == "verified_success"
        store.complete_handoff(security_handoff["id"], "security", result, "security")

        await pm.process_pending()
        await pm.process_pending()
        rows = store.connection.execute(
            "SELECT id FROM tasks WHERE json_extract(source,'$.kind')='change_intelligence'"
        ).fetchall()
        assert len(rows) == 1
        docs_task = store.get_task(rows[0]["id"])
        docs_handoff = store.connection.execute(
            "SELECT * FROM handoffs WHERE task_id=?", (docs_task["id"],)
        ).fetchone()
        report = await ChangeIntelligenceExecutor(store, "docs", [tmp_path / "qa"])(
            {"task_id": docs_task["id"], "handoff_id": docs_handoff["id"]}
        )
        assert report["status"] == "completed"
        assert report["revision"] == workflow.get(task_id)["revision"]
        assert report["change_report"]["diff_sha256"]
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_configured_project_without_gate_denies_release_evidence(tmp_path):
    flow = _new_flow(tmp_path)
    store, ledger, workflow, task_id, *_ = flow
    await implement_and_review(flow, tmp_path)
    _configure(flow, tmp_path)
    try:
        with pytest.raises(PermissionError, match="requires a security gate"):
            workflow.evidence(task_id)
        with pytest.raises(PermissionError, match="requires a security gate"):
            workflow.decide(
                task_id,
                "release",
                True,
                "owner",
                store.get_task(task_id)["version"],
            )
        assert workflow.get(task_id)["phase"] == "human_review"
    finally:
        store.close()
        ledger.close()
