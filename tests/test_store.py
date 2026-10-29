import sqlite3

import pytest

from switchcrew.store import ConflictError, InvalidTransitionError, Store


def task(store):
    return store.create_task("Build it", {"thread_id": "thread"}, ["Tests pass"], task_id="t")


def test_restart_and_duplicate_delivery(tmp_path):
    path = tmp_path / "db.sqlite"
    store = Store(path)
    task(store)
    handoff = store.create_handoff("t", "pm", "research", "Investigate", ["Evidence"], 0)
    assert store.receive_inbox("msg", "research", {"handoff_id": handoff["id"]}, "t")
    store.set_cursor("room", "42")
    store.close()
    store = Store(path)
    assert store.get_task("t")["version"] == 1
    assert len(store.list_events("t")) == 2
    assert store.get_cursor("room") == "42"
    assert len(store.pending_inbox("research")) == 1
    assert len(store.pending_outbox()) == 1
    assert not store.receive_inbox("msg", "research", {"handoff_id": handoff["id"]}, "t")
    store.accept_handoff(handoff["id"], "research")
    store.complete_handoff(handoff["id"], "research", {"answer": "done"})
    count = len(store.list_events("t"))
    store.complete_handoff(handoff["id"], "research", {"answer": "done"})
    assert len(store.list_events("t")) == count


def test_atomic_rollback_and_optimistic_concurrency(tmp_path):
    store = Store(tmp_path / "db")
    task(store)
    store.connection.execute(
        "CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox "
        "BEGIN SELECT RAISE(ABORT,'disk failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.transition_task("t", "AWAITING_APPROVAL", 0)
    assert store.get_task("t")["version"] == 0
    assert len(store.list_events("t")) == 1
    store.connection.execute("DROP TRIGGER fail_outbox")
    other = Store(tmp_path / "db")
    store.transition_task("t", "AWAITING_APPROVAL", 0)
    with pytest.raises(ConflictError):
        other.transition_task("t", "PLANNED", 0)
    assert len(store.list_events("t")) == 2


def test_validation_forgery_and_idempotency(tmp_path):
    store = Store(tmp_path / "db")
    with pytest.raises(ValueError):
        store.create_task(" ", {}, [])
    task(store)
    assert task(store)["id"] == "t"
    with pytest.raises(ConflictError):
        store.create_task("Changed", {}, [], "t")
    with pytest.raises(InvalidTransitionError):
        store.transition_task("t", "COMPLETED", 0)
    handoff = store.create_handoff("t", "pm", "research", "Q?", [], 0)
    with pytest.raises(PermissionError):
        store.accept_handoff(handoff["id"], "forged")
    with pytest.raises(InvalidTransitionError):
        store.complete_handoff(handoff["id"], "research", {})
    assert store.get_handoff(handoff["id"])["status"] == "pending"


def test_processing_checkpoint_atomic_with_outgoing(tmp_path):
    store = Store(tmp_path / "db")
    task(store)
    store.receive_inbox("m", "research", {}, "t")
    outgoing = [
        {"task_id": "t", "recipient": "pm", "payload": {"answer": 1}, "idempotency_key": "result"}
    ]
    store.finish_inbox("m", "research", outgoing)
    assert not store.finish_inbox("m", "research", outgoing)
    assert not store.pending_inbox("research")
    assert len(store.pending_outbox()) == 1
    item = store.pending_outbox()[0]
    store.mark_outbox_sent(item["id"], "remote-id")
    assert not store.pending_outbox()
    assert store.enqueue_outbox("t", "pm", {"answer": 1}, "result")["external_id"] == "remote-id"
    with pytest.raises(ConflictError):
        store.enqueue_outbox("t", "pm", {"answer": 2}, "result")


def test_shared_connection_concurrency_and_handoff_replay(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path / "db")
    task(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.receive_inbox("same", "pm", {}, "t"), range(12)))
    assert sum(results) == 1
    handoff = store.create_handoff("t", "pm", "research", "Question", [], 0, "stable-id")
    assert store.create_handoff("t", "pm", "research", "Question", [], 0, "stable-id") == handoff
    assert len(store.pending_outbox()) == 1
    assert store.healthcheck()


def test_atomic_inbox_checkpoint_rolls_back_on_outbox_failure(tmp_path):
    store = Store(tmp_path / "db")
    task(store)
    store.receive_inbox("m", "pm", {}, "t")
    outgoing = [dict(task_id="missing", recipient="pm", payload={}, idempotency_key="bad")]
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_inbox("m", "pm", outgoing)
    assert len(store.pending_inbox("pm")) == 1
    assert not store.pending_outbox()


def test_future_schema_rejected(tmp_path):
    path = tmp_path / "future.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=2")
    connection.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)
