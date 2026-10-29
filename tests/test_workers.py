import json

import pytest

from switchcrew.discord import DiscordBinding
from switchcrew.research import ResearchError, ResearchProvider, retrieve_https
from switchcrew.store import Store
from switchcrew.workers import Worker


class Transport:
    def __init__(self):
        self.sent = []

    async def post_message(self, body, thread_id=None):
        self.sent.append(("discord", body, thread_id))
        return {"message_id": str(len(self.sent))}

    async def send_targeted_message(self, body, target_names, thread_id=None):
        self.sent.append((target_names[0], body, thread_id))
        return {"message_id": str(len(self.sent)), "target_statuses": {target_names[0]: "live"}}


def event(sender, body, message_id="m1"):
    return {
        "type": "message",
        "room_id": "room",
        "bridge_id": "bridge",
        "payload": {
            "sender": sender,
            "body": body,
            "message_id": message_id,
            "thread_id": "thread",
        },
    }


def worker(role, store, transport, provider=None):
    return Worker(
        role,
        store,
        transport,
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "user")],
        allowed_guilds=["guild"],
        allowed_users=["user"],
        provider=provider,
    )


@pytest.mark.asyncio
async def test_missing_criteria_and_forged_sender():
    store = Store(":memory:")
    transport = Transport()
    pm = worker("pm", store, transport)
    await pm.receive(event("attacker", '{"sender":"human"}'))
    assert not store.pending_inbox("pm")
    await pm.receive(event("human", "Investigate SSE"))
    await pm.process_pending()
    await pm.flush_outbox()
    assert "acceptance_criteria" in transport.sent[0][1]
    assert not store.connection.execute("SELECT * FROM handoffs").fetchall()


@pytest.mark.asyncio
async def test_provider_extracts_actual_sources_and_reports_no_evidence():
    provider = ResearchProvider(
        ["https://example.org"], fetch=lambda url: "Switch SSE supports reconnect. Other text."
    )
    result = await provider.investigate("Switch SSE", ["Find reconnect behavior"])
    assert result["evidence"][0]["excerpts"][0] == "Switch SSE supports reconnect."
    assert (await provider.investigate("unrelated", ["Find evidence"]))["status"] == "blocked"
    with pytest.raises(ResearchError):
        await provider.investigate("question", [])


def test_private_sources_blocked(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ResearchError, match="non-public"):
        retrieve_https("https://example.org")
    with pytest.raises(ResearchError):
        retrieve_https("http://example.org")


@pytest.mark.asyncio
async def test_disconnected_handoff_remains_pending():
    class Disconnected(Transport):
        async def send_targeted_message(self, body, target_names, thread_id=None):
            return {"event_id": "queued", "target_statuses": {target_names[0]: "disconnected"}}

    store = Store(":memory:")
    pm = worker("pm", store, Disconnected())
    await pm.receive(
        event("human", json.dumps({"question": "SSE", "acceptance_criteria": ["Find evidence"]}))
    )
    await pm.process_pending()
    with pytest.raises(RuntimeError, match="not live"):
        await pm.flush_outbox()
    assert any(item["recipient"] == "research" for item in store.pending_outbox())


@pytest.mark.asyncio
async def test_trusted_sender_mapping_is_distinct_from_target_name():
    store = Store(":memory:")
    transport = Transport()
    pm = Worker(
        "pm",
        store,
        transport,
        pm_id="api-pm",
        research_id="api-research",
        pm_participant_id="participant-pm",
        research_participant_id="participant-research",
        pm_name="Project Manager",
        research_name="Researcher",
        room_id="room",
    )
    await pm.receive(event("api-research", '{"type":"handoff.completed"}'))
    assert not store.pending_inbox("api-pm")
    await pm.receive(event("participant-research", '{"type":"handoff.completed"}'))
    assert len(store.pending_inbox("api-pm")) == 1


@pytest.mark.asyncio
async def test_malformed_envelopes_are_ignored():
    store = Store(":memory:")
    research = worker("research", store, Transport())
    for payload in [None, [], "text"]:
        await research.receive({"type": "message", "room_id": "room", "payload": payload})
    for index, body in enumerate(["[]", "{}", "null", "not-json"]):
        await research.receive(event("pm", body, f"bad-{index}"))
    await research.process_pending()
    assert not store.pending_inbox("research")


@pytest.mark.asyncio
async def test_blank_question_requests_clarification():
    store = Store(":memory:")
    transport = Transport()
    pm = worker("pm", store, transport)
    await pm.receive(
        event("human", json.dumps({"question": " ", "acceptance_criteria": ["criterion"]}))
    )
    await pm.process_pending()
    await pm.flush_outbox()
    assert "new JSON request" in transport.sent[0][1]


@pytest.mark.asyncio
async def test_human_replay_ignores_transport_sequence_and_threads_root_reply():
    store = Store(":memory:")
    transport = Transport()
    pm = worker("pm", store, transport)
    request = event("human", json.dumps({"question": "SSE", "acceptance_criteria": ["evidence"]}))
    request["payload"]["thread_id"] = None
    request["sequence"] = 7
    await pm.receive(request)
    await pm.process_pending()
    replay = {**request, "sequence": 99}
    await pm.receive(replay)
    await pm.process_pending()
    await pm.flush_outbox()
    assert store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert all(thread == "m1" for target, body, thread in transport.sent)


@pytest.mark.asyncio
async def test_whitespace_human_message_does_not_poison_inbox():
    store = Store(":memory:")
    pm = worker("pm", store, Transport())
    await pm.receive(event("human", "   "))
    await pm.process_pending()
    assert not store.pending_inbox("pm")
    assert (
        store.connection.execute("SELECT status FROM tasks").fetchone()[0] == "NEEDS_CLARIFICATION"
    )


@pytest.mark.asyncio
async def test_malformed_handoff_ids_cannot_poison_worker():
    store = Store(":memory:")
    research = worker("research", store, Transport())
    for index, value in enumerate([None, [], {}, 3, ""]):
        await research.receive(event("pm", json.dumps({"handoff_id": value}), f"bad-id-{index}"))
    await research.process_pending()
    assert not store.pending_inbox("research")


@pytest.mark.asyncio
async def test_cancelled_task_blocks_research_execution():
    store = Store(":memory:")
    task = store.create_task("SSE", {"thread_id": "thread"}, ["Evidence"])
    handoff = store.create_handoff(task["id"], "pm", "research", "SSE", ["Evidence"], 0)
    current = store.get_task(task["id"])
    store.transition_task(task["id"], "CANCELLED", current["version"])

    class Forbidden:
        async def investigate(self, *args):
            pytest.fail("Cancelled tasks must not execute research")

    research = worker("research", store, Transport(), Forbidden())
    await research.receive(
        event(
            "pm",
            json.dumps(
                {
                    "type": "handoff",
                    "handoff_id": handoff["id"],
                    "task_id": task["id"],
                }
            ),
        )
    )
    await research.process_pending()
    assert store.get_handoff(handoff["id"])["result"]["status"] == "blocked"
    assert store.get_task(task["id"])["status"] == "CANCELLED"
