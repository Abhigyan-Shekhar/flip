from types import SimpleNamespace

import pytest

from switchcrew.learning_runtime import lesson_prompt
from switchcrew.limits import consume_budget
from switchcrew.store import Store


async def test_lesson_prompt_snapshot_replay_and_actor_boundary():
    store = Store(":memory:")
    task = store.create_task("Fix boundary", {"project_id": "project"}, ["Regression"])
    calls = []

    async def retrieve(query, **kwargs):
        calls.append((query, kwargs))
        return {"lessons": [{"id": "lesson-" + "a" * 24, "correction": "Check the boundary"}]}

    service = SimpleNamespace(retrieve_lessons=retrieve)
    state = {"task_id": task["id"], "handoff_id": "assignment", "question": "Fix boundary"}
    prompt = await lesson_prompt(store, service, state, actor_id="engineering")
    assert "Untrusted approved failure lessons" in prompt["question"]
    assert "used_lesson_ids" in prompt["question"]
    assert await lesson_prompt(store, service, state, actor_id="engineering") == prompt
    assert len(calls) == 1
    with pytest.raises(PermissionError):
        await lesson_prompt(store, service, state, actor_id="other")
    assert state["question"] == "Fix boundary"
    store.close()


def test_durable_rate_limit_duplicate_event_and_window_recovery(tmp_path):
    store = Store(tmp_path / "limits.db")
    assert consume_budget(store, "actor", "one", limit=1, now=10)
    assert consume_budget(store, "actor", "one", limit=1, now=11)
    assert not consume_budget(store, "actor", "two", limit=1, now=12)
    store.close()
    reopened = Store(tmp_path / "limits.db")
    assert not consume_budget(reopened, "actor", "two", limit=1, now=13)
    assert consume_budget(reopened, "actor", "two", limit=1, now=70)
    reopened.close()
