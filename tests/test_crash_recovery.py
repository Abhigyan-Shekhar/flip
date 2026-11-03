"""Actual abrupt process death at SQLite transaction/outbox boundaries, no live-service claim."""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from switchcrew.store import Store


@pytest.mark.parametrize("boundary", ["before_commit", "after_handoff_commit"])
def test_sigkill_recovers_atomic_state_and_pending_delivery(tmp_path, boundary):
    database = tmp_path / "crash.db"
    code = """
import os, signal, sys
from switchcrew.store import Store
store = Store(sys.argv[1])
task = store.create_task("Crash acceptance fixture", {}, ["One logical handoff"], "task")
if sys.argv[2] == "before_commit":
    with store.transaction():
        store.transition_task("task", "PLANNED", 0)
        os.kill(os.getpid(), signal.SIGKILL)
else:
    task = store.transition_task("task", "PLANNED", 0)
    task = store.transition_task("task", "IN_PROGRESS", task["version"])
    store.create_handoff("task", "pm", "research", task["objective"], task["acceptance_criteria"],
                         task["version"], handoff_id="durable-handoff")
    os.kill(os.getpid(), signal.SIGKILL)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(database), boundary],
        env={"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        timeout=10,
    )
    assert process.returncode == -signal.SIGKILL
    recovered = Store(database)
    try:
        task = recovered.get_task("task")
        if boundary == "before_commit":
            assert task["status"] == "DRAFT" and task["version"] == 0
            assert len(recovered.list_events("task")) == 1
            assert not recovered.pending_outbox()
        else:
            assert task["status"] == "IN_PROGRESS"
            pending = [o for o in recovered.pending_outbox() if o["recipient"] == "research"]
            assert len(pending) == 1
            handoff = recovered.get_handoff("durable-handoff")
            assert (
                recovered.create_handoff(
                    "task",
                    "pm",
                    "research",
                    task["objective"],
                    task["acceptance_criteria"],
                    task["version"],
                    handoff_id="durable-handoff",
                )
                == handoff
            )
            assert [
                o for o in recovered.pending_outbox() if o["recipient"] == "research"
            ] == pending
            recovered.accept_handoff(handoff["id"], "research")
            recovered.complete_handoff(handoff["id"], "research", {"status": "evidence_found"})
            recovered.mark_outbox_sent(pending[0]["id"], "controlled-delivery")
            assert recovered.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    finally:
        recovered.close()
