"""Deterministic in-process collaboration test; does not validate live Switch/Discord."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_workers import Transport, event, worker

from switchcrew.discord import DiscordBinding
from switchcrew.research import ResearchProvider
from switchcrew.store import Store
from switchcrew.wire import read_message
from switchcrew.workers import Worker


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
            read_message(body, store)["type"] == "handoff.accepted"
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
                if read_message(body, store)["type"] == "handoff.accepted"
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


@pytest.mark.asyncio
async def test_request_only_in_gap_history_completes_once_in_original_thread_after_restart(
    tmp_path,
):
    """A missed request, overlapping history pages and live replay share one inbox ID."""
    request = event(
        "human",
        json.dumps({"question": "Gateway intents", "acceptance_criteria": ["Find sources"]}),
        "missed-request",
    )
    root = {"id": "thread", "sender": "pm", "body": "Already bridged anchor"}
    page = {
        "threads": [
            {
                "root": root,
                "replies": [
                    {
                        "id": request["payload"]["message_id"],
                        "sender": "human",
                        "body": request["payload"]["body"],
                    }
                ],
            }
        ]
    }

    class GapTransport(Transport):
        cursor = None

        async def connect_to_room(self, room):
            assert room == "room"

        async def acknowledge(self, sequence):
            self.cursor = sequence

        async def events(self):
            yield {"type": "connection_state", "generation": 1}
            # History pages can overlap; the subsequent live stream may repeat the request.
            yield {"type": "gap", "resumed_at": 40, "history": {"room": [page, page]}}
            assert store.get_cursor("pm") == "40"
            yield {**request, "sequence": 41}

    path = tmp_path / "gap.db"
    store = Store(path)
    pm_bus = GapTransport()
    await worker("pm", store, pm_bus).run()
    assert store.get_cursor("pm") == "41"
    store.close()

    store = Store(path)
    pm = worker("pm", store, pm_bus)
    research_bus = Transport()
    fetches = []

    def fetch(url):
        fetches.append(url)
        return "Gateway intents select events."

    research = worker(
        "research",
        store,
        research_bus,
        ResearchProvider(["https://example.org"], fetch=fetch),
    )
    handoff = next(body for target, body, _ in pm_bus.sent if target == "research")
    await research.receive(event("pm", handoff, "handoff"))
    await research.process_pending()
    await research.flush_outbox()
    for index, (_, body, thread) in enumerate(research_bus.sent):
        assert thread == "thread"
        await pm.receive(event("research", body, f"result-{index}"))
    await pm.process_pending()
    await pm.flush_outbox()
    # A second replay after completion must not create work or re-run retrieval.
    await research.receive(event("pm", handoff, "handoff-retry"))
    await research.process_pending()
    await pm.receive(request)
    await pm.process_pending()
    await pm.flush_outbox()

    task = store._row(store.connection.execute("SELECT * FROM tasks").fetchone())
    assert task["status"] == "COMPLETED"
    assert task["source"]["thread_id"] == "thread"
    assert task["source"]["message_id"] == "missed-request"
    assert store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    assert len(fetches) == 1
    assert all(thread == "thread" for _, _, thread in pm_bus.sent)
    assert sum("Research result:" in body for _, body, _ in pm_bus.sent) == 1
    store.close()


@pytest.mark.asyncio
async def test_failed_history_persistence_does_not_advance_gap_cursor(tmp_path):
    class GapTransport(Transport):
        cursor = None

        async def connect_to_room(self, room):
            pass

        async def events(self):
            yield {"type": "connection_state"}
            yield {
                "type": "gap",
                "resumed_at": 40,
                "history": {
                    "room": [
                        {
                            "threads": [
                                {
                                    "root": {
                                        "id": "lost-request",
                                        "sender": "human",
                                        "body": json.dumps(
                                            {
                                                "question": "Gateway",
                                                "acceptance_criteria": ["Sources"],
                                            }
                                        ),
                                    }
                                }
                            ]
                        }
                    ]
                },
            }

    class InterruptedWorker(Worker):
        async def receive(self, event):
            if event.get("payload", {}).get("message_id") == "lost-request":
                raise RuntimeError("interrupted before inbox persistence")
            await super().receive(event)

    store = Store(tmp_path / "interrupted-gap.db")
    store.set_cursor("pm", 7)
    pm = InterruptedWorker(
        "pm",
        store,
        GapTransport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "user")],
        allowed_guilds=["guild"],
        allowed_users=["user"],
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        await pm.run()
    assert store.get_cursor("pm") == "7"
    await worker("pm", store, GapTransport()).run()
    assert store.get_cursor("pm") == "40"
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    store.close()


def test_gap_cursor_injection_is_reversible_but_cannot_overwrite_replay_progress(tmp_path):
    path, backup = tmp_path / "application.db", tmp_path / "gap-backup.json"
    store = Store(path)
    store.set_cursor("pm", 7)
    store.close()
    script = Path(__file__).resolve().parents[1] / "deploy/switch/gap_cursor.py"

    def invoke(action, *, stopped=True):
        args = [
            sys.executable,
            str(script),
            action,
            "--database",
            str(path),
            "--agent-id",
            "pm",
            "--backup",
            str(backup),
        ]
        if stopped:
            args.append("--workers-stopped")
        return subprocess.run(args, capture_output=True, text=True, timeout=10)

    assert invoke("arm", stopped=False).returncode != 0
    assert not backup.exists()
    assert invoke("arm").returncode == 0
    original_backup = backup.read_bytes()
    assert not (backup.stat().st_mode & 0o077)
    assert invoke("arm").returncode == 1
    assert backup.read_bytes() == original_backup
    assert invoke("restore").returncode == 0
    store = Store(path)
    assert store.get_cursor("pm") == "7"
    store.close()
    # A pre-existing backup must never be overwritten on another arm attempt.
    assert invoke("arm").returncode == 1
    store = Store(path)
    assert store.get_cursor("pm") == "7"
    store.set_cursor("pm", 55)  # The worker has made legitimate progress.
    store.close()
    assert invoke("restore").returncode == 1
    store = Store(path)
    assert store.get_cursor("pm") == "55"
    store.close()
