import pytest

from switchcrew.engineering import enqueue_independent_qa
from switchcrew.store import Store


@pytest.mark.parametrize("terminal_status", ["CANCELLED", "COMPLETED"])
def test_terminal_task_does_not_create_qa_handoff(tmp_path, terminal_status):
    store = Store(tmp_path / "state.db")
    task = store.create_task("Implement", {}, ["Tests pass"])
    handoff = store.create_handoff(
        task["id"], "coordinator", "engineering", "Implement", ["Tests pass"], 0
    )
    store.accept_handoff(handoff["id"], "engineering")
    store.complete_handoff(
        handoff["id"], "engineering", {"status": "implemented", "final_revision": "abc"}
    )
    if terminal_status == "COMPLETED":
        task = store.transition_task(task["id"], "PLANNED", 1)
        task = store.transition_task(task["id"], "IN_PROGRESS", task["version"])
        store.transition_task(task["id"], "COMPLETED", task["version"])
    else:
        store.transition_task(task["id"], "CANCELLED", 1)
    assert enqueue_independent_qa(store, handoff["id"], "engineering", "qa") is None
    assert not [item for item in store.pending_outbox() if item["recipient"] == "qa"]
    store.close()
