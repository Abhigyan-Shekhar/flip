"""Deterministic in-process collaboration test; does not validate live Switch/Discord."""

import json

import pytest
from test_workers import Transport, event, worker

from switchcrew.research import ResearchProvider
from switchcrew.store import Store


@pytest.mark.asyncio
async def test_handoff_ack_result_and_restart(tmp_path):
    path = tmp_path / "state.db"
    store = Store(path)
    pm_bus, research_bus = Transport(), Transport()
    pm = worker("pm", store, pm_bus)
    request = event(
        "human",
        json.dumps({"question": "Switch SSE", "acceptance_criteria": ["Find reconnect evidence"]}),
    )
    await pm.receive(request)
    await pm.process_pending()
    await pm.flush_outbox()
    delegation = next(body for target, body, thread in pm_bus.sent if target == "research")
    store.close()
    store = Store(path)
    pm = worker("pm", store, pm_bus)
    calls = []

    def fetch(url):
        assert any(
            json.loads(body.split("\n")[1])["type"] == "handoff.accepted"
            for target, body, thread in research_bus.sent
        )
        calls.append(url)
        return "Switch SSE reconnects."

    research = worker(
        "research", store, research_bus, ResearchProvider(["https://example.org"], fetch=fetch)
    )
    await research.receive(event("pm", delegation, "delegation"))
    await research.process_pending()
    await research.flush_outbox()
    for index, (_target, body, thread) in enumerate(research_bus.sent):
        assert thread == "thread"
        await pm.receive(event("research", body, f"result-{index}"))
    await pm.process_pending()
    await pm.flush_outbox()
    assert any(
        "Research result:" in body and "Switch SSE reconnects." in body
        for target, body, thread in pm_bus.sent
    )
    await research.receive(event("pm", delegation, "delegation-retry"))
    await research.process_pending()
    assert len(calls) == 1
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    await pm.receive(request)
    await pm.process_pending()
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    assignment = store.connection.execute("SELECT agent,status FROM assignments").fetchone()
    assert tuple(assignment) == ("research", "completed")


@pytest.mark.asyncio
async def test_restart_after_research_acceptance(tmp_path):
    path = tmp_path / "recovery.db"
    store = Store(path)
    pm_bus, research_bus = Transport(), Transport()
    pm = worker("pm", store, pm_bus)
    await pm.receive(
        event("human", json.dumps({"question": "SSE", "acceptance_criteria": ["Find evidence"]}))
    )
    await pm.process_pending()
    await pm.flush_outbox()
    delegation = next(body for target, body, thread in pm_bus.sent if target == "research")

    class Interrupted:
        async def investigate(self, question, acceptance_criteria):
            raise RuntimeError("process interrupted")

    research = worker("research", store, research_bus, Interrupted())
    await research.receive(event("pm", delegation, "handoff"))
    with pytest.raises(RuntimeError, match="interrupted"):
        await research.process_pending()
    assert store.connection.execute("SELECT status FROM handoffs").fetchone()[0] == "accepted"
    store.close()
    store = Store(path)
    research = worker(
        "research",
        store,
        research_bus,
        ResearchProvider(["https://example.org"], fetch=lambda _: "SSE evidence."),
    )
    await research.process_pending()
    await research.flush_outbox()
    assert store.connection.execute("SELECT status FROM handoffs").fetchone()[0] == "completed"
    assert (
        len(
            [
                body
                for target, body, thread in research_bus.sent
                if json.loads(body.split("\n")[1])["type"] == "handoff.accepted"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_gap_recovers_verified_human_history():
    class HistoryTransport(Transport):
        cursor = None

        async def connect_to_room(self, room):
            assert room == "room"

        async def acknowledge(self, sequence):
            self.cursor = sequence

        async def events(self):
            yield {"type": "connection_state"}
            assert self.operations == [("release_role", {}), ("assume_role", {"role": "pm"})]
            yield {
                "type": "gap",
                "resumed_at": 20,
                "history": {
                    "room": [
                        {
                            "threads": [
                                {
                                    "root": {
                                        "id": "historical",
                                        "sender": "human",
                                        "body": json.dumps(
                                            {
                                                "question": "SSE",
                                                "acceptance_criteria": ["Find evidence"],
                                            }
                                        ),
                                    },
                                    "replies": [],
                                }
                            ]
                        }
                    ]
                },
            }

    store = Store(":memory:")
    transport = HistoryTransport()
    pm = worker("pm", store, transport)
    await pm.run()
    assert store.get_cursor("pm") == "20"
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    assert any(target == "research" for target, body, thread in transport.sent)


@pytest.mark.asyncio
async def test_gap_cannot_remove_proxy_sender_checks():
    class HistoryTransport(Transport):
        cursor = None

        async def connect_to_room(self, room):
            pass

        async def events(self):
            yield {"type": "connection_state"}
            yield {
                "type": "gap",
                "resumed_at": 20,
                "history": {
                    "room": [
                        {
                            "threads": [
                                {
                                    "root": {
                                        "id": "proxy-message",
                                        "sender": "human",
                                        "kind": "message",
                                        "sender_kind": "platform",
                                        "on_behalf_of": "someone",
                                        "body": json.dumps(
                                            {"question": "SSE", "acceptance_criteria": ["Sources"]}
                                        ),
                                    },
                                    "replies": [],
                                }
                            ]
                        }
                    ]
                },
            }

    store = Store(":memory:")
    await worker("pm", store, HistoryTransport()).run()
    assert store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
