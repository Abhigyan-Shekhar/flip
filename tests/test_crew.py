import uuid

from switchcrew.crew import handle_crew_command
from switchcrew.discord import DiscordSource
from switchcrew.orchestration import Orchestrator
from switchcrew.store import Store


def source(body, *, user="human", room="room", thread="thread"):
    return DiscordSource(
        "guild", "channel", user, room, "participant", "message-new", thread, body, ()
    )


def create(store, *, owner="human", room="room", thread="thread", status="IN_PROGRESS"):
    task_id = str(uuid.uuid4())
    task = store.create_task(
        "SECRET OBJECTIVE",
        {"room_id": room, "thread_id": thread, "message_id": "original", "user_id": owner},
        ["SECRET CRITERIA"],
        task_id,
    )
    if status != "DRAFT":
        task = store.transition_task(task_id, "PLANNED", task["version"])
        task = store.transition_task(task_id, "IN_PROGRESS", task["version"])
    return task


def test_commands_are_strict_and_scope_is_private():
    store = Store(":memory:")
    task = create(store)
    malformed = handle_crew_command(store, source("/crew status nope"))
    assert malformed["handled"] and malformed["task_id"] is None
    assert handle_crew_command(store, source("hello")) is None
    other_room = handle_crew_command(store, source(f"/crew status {task['id']}", room="elsewhere"))
    other_thread = handle_crew_command(
        store, source(f"/crew status {task['id']}", thread="elsewhere")
    )
    assert other_room["handled"] and other_room["task_id"] is None
    assert other_thread["handled"] and other_thread["task_id"] is None
    assert "SECRET" not in other_room["text"]
    assert "SECRET" not in handle_crew_command(store, source(f"/crew status {task['id']}"))["text"]


def test_cancel_owner_approver_publishing_and_repeat():
    store = Store(":memory:")
    task = create(store)
    denied = handle_crew_command(store, source(f"/crew cancel {task['id']}", user="intruder"))
    assert "not authorized" in denied["text"]
    assert store.get_task(task["id"])["status"] == "IN_PROGRESS"
    assert "cancelled" in handle_crew_command(store, source(f"/crew cancel {task['id']}"))["text"]
    assert (
        "already cancelled"
        in handle_crew_command(store, source(f"/crew cancel {task['id']}"))["text"]
    )

    task2 = create(store)
    assert (
        "cancelled"
        in handle_crew_command(
            store, source(f"/crew cancel {task2['id']}", user="approver"), can_cancel=True
        )["text"]
    )
    task3 = create(store, status="DRAFT")
    for next_status in (
        "AWAITING_APPROVAL",
        "PLANNED",
        "IN_PROGRESS",
        "QA_REVIEW",
        "PASSED",
        "HUMAN_REVIEW",
        "PUBLISH_APPROVED",
        "PUBLISHING",
    ):
        task3 = store.transition_task(task3["id"], next_status, task3["version"])
    assert (
        "cannot be cancelled"
        in handle_crew_command(store, source(f"/crew cancel {task3['id']}"))["text"]
    )


def test_team_and_replay_use_recorded_allowlisted_fields_only():
    store = Store(":memory:")
    task = create(store, status="DRAFT")
    orch = Orchestrator(store, "pm", {"incident": "inc-agent", "research": "research-agent"})
    orch.start_incident(
        task["id"],
        {
            "category": "service_failure",
            "severity": "SEV2",
            "summary": "safe summary",
            "evidence_refs": [],
        },
    )
    orch.reconcile(task["id"])
    team = handle_crew_command(store, source(f"/crew team {task['id']}"))["text"]
    why = handle_crew_command(store, source(f"/crew why {task['id']}"))["text"]
    assert "incident" in team and "research" in team
    assert "Leads triage" in why and "evidence gathering" in why
    handoff_id = orch.get(task["id"])["steps"]["step-1"]["handoff_id"]
    store._event(
        task["id"],
        "handoff.completed",
        {
            "handoff_id": handoff_id,
            "sender": "research-agent",
            "result": {
                "raw_tool_output": "LEAK_TOOL",
                "objective": "LEAK_OBJECTIVE",
                "diff": "LEAK_DIFF",
                "prompt": "LEAK_PROMPT",
                "token": "LEAK_TOKEN",
                "path": "LEAK_PATH",
            },
        },
        store.get_task(task["id"])["version"],
    )
    replay = handle_crew_command(store, source(f"/crew replay {task['id']}"))["text"]
    assert handoff_id in replay and "research-agent" in replay
    for secret in (
        "LEAK_TOOL",
        "LEAK_OBJECTIVE",
        "LEAK_DIFF",
        "LEAK_PROMPT",
        "LEAK_TOKEN",
        "LEAK_PATH",
        "SECRET OBJECTIVE",
    ):
        assert secret not in replay


def test_untrusted_plan_fields_are_not_rendered():
    store = Store(":memory:")
    task = create(store, status="DRAFT")
    with store._lock:
        store.connection.execute(
            "CREATE TABLE plans (task_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        store.connection.execute(
            "INSERT INTO plans VALUES (?, ?)",
            (
                task["id"],
                '{"team":["research"],"steps":{},"selection_reasons":{"research":{"reason":"safe","prompt":"DO_NOT_SHOW"}}}',
            ),
        )
    result = handle_crew_command(store, source(f"/crew why {task['id']}"))["text"]
    assert "safe" in result and "DO_NOT_SHOW" not in result


def test_replay_retains_valid_hashed_handoff_ids_without_unredacting_arbitrary_tokens(tmp_path):
    from switchcrew.discord import DiscordSource
    from switchcrew.store import Store

    store = Store(tmp_path / "hashed.db")
    task = store.create_task("Private", {"room_id": "room", "thread_id": "thread"}, ["Check"])
    identity = "engineering-" + "a" * 64
    store._event(
        task["id"],
        "handoff.created",
        {"handoff_id": identity, "sender": "Bearer sensitive-secret", "recipient": "engineering"},
        0,
    )
    source = DiscordSource(
        "guild",
        "channel",
        "owner",
        "room",
        "human",
        "message",
        "thread",
        "/crew replay " + task["id"],
        (),
    )
    result = handle_crew_command(store, source)
    assert identity in result["text"]
    assert "sensitive-secret" not in result["text"]
    store.close()
