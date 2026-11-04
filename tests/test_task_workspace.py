import json
from dataclasses import replace

from fastapi.testclient import TestClient
from test_crew import create, source
from test_workers import Transport, event

from switchcrew.api import create_app
from switchcrew.crew import handle_crew_command
from switchcrew.discord import DiscordBinding
from switchcrew.settings import Settings
from switchcrew.store import Store
from switchcrew.trace import execution_trace
from switchcrew.workers import Worker


def test_channel_lists_filter_employee_status_and_hide_other_channels():
    store = Store(":memory:")
    visible = store.create_task(
        "Private",
        {
            "room_id": "room",
            "guild_id": "guild",
            "channel_id": "channel",
            "thread_id": "other-thread",
            "user_id": "human",
            "assigned_employee": "ria",
        },
        ["Check"],
    )
    hidden = store.create_task(
        "Secret",
        {"room_id": "room", "guild_id": "guild", "channel_id": "private", "thread_id": "hidden"},
        ["Check"],
    )
    result = handle_crew_command(store, source("/crew tasks employee ria"))
    assert visible["id"] in result["text"] and hidden["id"] not in result["text"]
    assert "Private" not in result["text"]
    assert visible["id"] not in handle_crew_command(store, source("/crew completed"))["text"]
    assert visible["id"] in handle_crew_command(store, source("/crew mine"))["text"]
    assert (
        visible["id"]
        not in handle_crew_command(store, source("/crew tasks", room="private"))["text"]
    )
    store.close()


async def test_text_create_dispatches_existing_pm_research_and_list_reply_is_durable(tmp_path):
    store = Store(tmp_path / "tasks.db")
    pm = Worker(
        "pm",
        store,
        Transport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_guilds=["guild"],
        allowed_users=["owner"],
    )
    request = event("human", "/crew create Find evidence | Cite official source")
    await pm._request(request)
    task = store._row(store.connection.execute("SELECT * FROM tasks").fetchone())
    assert task["status"] == "IN_PROGRESS" and task["source"]["channel_id"] == "channel"
    assert store.connection.execute("SELECT recipient FROM handoffs").fetchone()[0] == "research"
    listing = event("human", "/crew tasks", message_id="listing")
    await pm._request(listing)
    count = len(store.pending_outbox())
    await pm._request(listing)
    assert len(store.pending_outbox()) == count
    store.close()


def test_trace_is_lineage_aware_and_does_not_export_private_payloads():
    store = Store(":memory:")
    task = create(store, status="DRAFT")
    child = store.create_task("hidden child text", {"parent_task_id": task["id"]}, ["Secret"])
    store._event(
        child["id"],
        "qa.completed",
        {
            "result": {
                "status": "qa_failed",
                "tested_revision": "a" * 40,
                "raw_tool_output": "PRIVATE_OUTPUT",
                "commands": [{"exit_code": 1, "output_summary": "SECRET_SUMMARY"}],
            },
            "private_memory": "PRIVATE_MEMORY",
            "transcript": "PRIVATE_TRANSCRIPT",
        },
        0,
    )
    trace = execution_trace(store, task["id"])
    assert len(trace["tasks"]) == 2
    encoded = json.dumps(trace)
    assert "qa_failed" in encoded and "a" * 40 in encoded
    assert all(
        text not in encoded
        for text in (
            "PRIVATE_OUTPUT",
            "SECRET_SUMMARY",
            "PRIVATE_MEMORY",
            "PRIVATE_TRANSCRIPT",
            "hidden child text",
        )
    )
    wrong_thread = handle_crew_command(
        store, replace(source("/crew trace " + task["id"]), thread_id="elsewhere")
    )
    assert wrong_thread["task_id"] is None
    store.close()


def test_task_list_and_trace_apis_require_auth_and_validate_filters(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "api.db", api_token="a" * 40)
    with TestClient(create_app(config)) as client:
        assert client.get("/tasks").status_code == 401
        headers = {"Authorization": "Bearer " + "a" * 40}
        task = client.post(
            "/tasks", headers=headers, json={"objective": "Check", "acceptance_criteria": ["Cite"]}
        ).json()
        assert client.get("/tasks", headers=headers).json()[0]["id"] == task["id"]
        assert client.get("/tasks?status=invalid", headers=headers).status_code == 422
        assert client.get("/tasks?limit=1000", headers=headers).status_code == 422
        assert client.get(f"/tasks/{task['id']}/trace", headers=headers).json()[
            "private_payloads_omitted"
        ]


def test_switch_result_envelope_omits_private_evidence_and_verifies_digest():
    from switchcrew.wire import result_matches, transport_payload

    handoff = {"result": {"status": "completed", "transcript": "PRIVATE", "code_diff": "SECRET"}}
    wire = transport_payload({"type": "handoff.completed", "handoff_id": "h1", **handoff})
    assert "PRIVATE" not in json.dumps(wire) and "SECRET" not in json.dumps(wire)
    assert result_matches(wire, handoff)
    assert not result_matches(wire, {"result": {"status": "different"}})
    assert result_matches({"type": "handoff.accepted", "result_sha256": "old"}, handoff)


def test_mine_uses_verified_employee_actor_mapping_without_showing_unassigned_work():
    store = Store(":memory:")
    origin = {
        "room_id": "room",
        "guild_id": "guild",
        "channel_id": "channel",
        "thread_id": "thread",
        "user_id": "manager",
    }
    assigned = store.create_task("Employee work", origin, ["Check"])
    other = store.create_task("Other work", origin, ["Check"])
    store.connection.execute(
        "CREATE TABLE employee_registry (employee_id TEXT, actor_id TEXT, enabled INTEGER)"
    )
    store.connection.execute(
        "CREATE TABLE employee_assignments (task_id TEXT, employee_id TEXT, status TEXT)"
    )
    store.connection.execute("INSERT INTO employee_registry VALUES('ria','verified-ria',1)")
    store.connection.execute(
        "INSERT INTO employee_assignments VALUES(?, 'ria', 'active')", (assigned["id"],)
    )
    result = handle_crew_command(store, source("/crew mine", user="verified-ria"))
    assert assigned["id"] in result["text"]
    assert other["id"] not in result["text"]
    store.close()


async def test_revoked_meeting_preview_is_suppressed_before_discord_delivery():
    from types import SimpleNamespace

    store = Store(":memory:")
    task = store.create_task(
        "Notes",
        {"kind": "meeting_notes", "meeting_id": "m1", "room_id": "room", "thread_id": "thread"},
        ["Notes"],
    )
    task = store.transition_task(task["id"], "PLANNED", task["version"])
    task = store.transition_task(task["id"], "IN_PROGRESS", task["version"])
    handoff = store.create_handoff(task["id"], "pm", "notes", "Notes", ["Notes"], task["version"])
    store.accept_handoff(handoff["id"], "notes")
    store.complete_handoff(
        handoff["id"],
        "notes",
        {
            "status": "completed",
            "notes": {"action_items": [{"proposal_id": "p1", "title": "Private meeting action"}]},
        },
    )
    transport = Transport()
    pm = Worker(
        "pm",
        store,
        transport,
        pm_id="pm",
        research_id="research",
        room_id="room",
        specialist_targets={"notes": {"agent_id": "notes", "name": "notes"}},
    )
    pm.meetings = SimpleNamespace(_consents=lambda meeting_id: True)
    pm._reconcile_auxiliary()
    assert any(item["payload"]["type"] == "meeting_proposals" for item in store.pending_outbox())

    def revoked(meeting_id):
        raise PermissionError("revoked")

    pm.meetings._consents = revoked
    await pm.flush_outbox()
    assert all("Private meeting action" not in body for _, body, _ in transport.sent)
    assert (
        store.connection.execute(
            "SELECT status FROM outbox WHERE json_extract(payload,'$.type')='meeting_proposals'"
        ).fetchone()[0]
        == "suppressed"
    )
    store.close()
