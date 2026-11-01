"""Integration boundaries shared by the new executors and recovery transport."""

import asyncio
import json

import pytest

from switchcrew.engineering_qa import IntegrationRuntime
from switchcrew.runtime import AgentConnection
from switchcrew.store import Store
from switchcrew.switch import SwitchOwnershipLost


async def test_receipt_poll_waits_for_room_claim_not_just_stream_generation():
    claimed = asyncio.Event()
    entered = asyncio.Event()

    class Switch:
        agent_id = "engineering"
        generation = 1  # Assigned before the connection_state frame reaches the runtime.

        async def connect_to_room(self, room):
            entered.set()
            await claimed.wait()

    connection = AgentConnection(
        "engineering", "engineering", "sender", "engineering", "key", "room"
    )
    store = Store(":memory:")
    runtime = IntegrationRuntime(connection, store, Switch(), None)
    calls = []

    async def process():
        calls.append("executed")

    runtime.process_pending = process
    connecting = asyncio.create_task(runtime.on_connected())
    try:
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(runtime.poll(), 0.02)
        assert not calls
        assert not runtime.ready.is_set()
        claimed.set()
        await connecting
        assert runtime.ready.is_set()
        # The client calls this even when reconnection happens inside events()
        # and the prior generation remains known during reconnect backoff.
        runtime.switch.on_disconnect()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(runtime.poll(), 0.02)
        assert not runtime.ready.is_set()
        assert not calls
    finally:
        connecting.cancel()
        await connecting
        store.close()


@pytest.mark.parametrize("kind, expected", [("ownership", 0), ("ordinary", 1), ("mixed", 1)])
async def test_integration_supervisor_classifies_grouped_errors_without_secret_traceback(
    monkeypatch, caplog, kind, expected
):
    from switchcrew import engineering_qa

    async def fail(config):
        lost = SwitchOwnershipLost("private-credential")
        ordinary = RuntimeError("private-credential")
        errors = {"ownership": [lost], "ordinary": [ordinary], "mixed": [lost, ordinary]}
        raise ExceptionGroup("private-credential", errors[kind])

    monkeypatch.setattr(engineering_qa, "run", fail)
    assert await engineering_qa.run_integration("unused") == expected
    assert "private-credential" not in caplog.text
    assert ("integration_ownership_lost" if expected == 0 else "integration_failed") in caplog.text


async def test_integration_rejects_credentialed_origin_without_printing_it(tmp_path, caplog):
    from switchcrew.engineering_qa import run_integration

    config = tmp_path / "private.json"
    config.write_text(json.dumps({"switch_url": "https://user:private-credential@example.org"}))
    config.chmod(0o600)
    assert await run_integration(config) == 1
    assert "private-credential" not in caplog.text


async def test_generic_runtime_stands_down_on_terminal_transport_error(monkeypatch, caplog):
    from switchcrew import runtime

    async def fail(*args):
        raise SwitchOwnershipLost("private-credential")

    monkeypatch.setattr(runtime, "_main", fail)
    assert await runtime.run_runtime("engineering", "unused", None, None) == 0
    assert "private-credential" not in caplog.text
    assert "runtime_ownership_lost" in caplog.text


async def test_disconnect_during_ack_pauses_executor_until_reconnected(tmp_path):
    from test_runtime import setup_runtime

    runtime, event, handoff, calls = setup_runtime(tmp_path)
    await runtime.receive(event)
    runtime.switch.connected = False
    await runtime.process_pending()
    assert not runtime.switch.sent and not calls

    send = runtime.switch.send_targeted_message

    async def disconnect_after_ack(*args, **kwargs):
        result = await send(*args, **kwargs)
        runtime.switch.connected = False
        return result

    runtime.switch.connected = True
    runtime.switch.send_targeted_message = disconnect_after_ack
    await runtime.process_pending()
    assert not calls
    assert runtime.store.get_handoff(handoff["id"])["status"] == "accepted"
    assert runtime.store.pending_inbox("research")
    runtime.switch.connected = True
    runtime.switch.send_targeted_message = send
    await runtime.process_pending()
    assert len(calls) == 1
    runtime.store.close()


async def test_disconnect_during_send_retains_later_outbox_items(tmp_path):
    from test_runtime import setup_runtime

    runtime, _, first, _ = setup_runtime(tmp_path)
    task = runtime.store.get_task(first["task_id"])
    second = runtime.store.create_handoff(
        task["id"], "pm", "research", "Second question", ["Evidence"], task["version"]
    )
    runtime.store.accept_handoff(first["id"], "research")
    runtime.store.accept_handoff(second["id"], "research")
    send = runtime.switch.send_targeted_message
    runtime.switch.connected = True

    async def disconnect_after_send(*args, **kwargs):
        result = await send(*args, **kwargs)
        runtime.switch.connected = False
        return result

    runtime.switch.send_targeted_message = disconnect_after_send
    await runtime.flush_outbox()
    assert len(runtime.switch.sent) == 1
    remaining = [item for item in runtime.store.pending_outbox() if item["recipient"] == "pm"]
    assert len(remaining) == 1
    runtime.switch.connected = True
    runtime.switch.send_targeted_message = send
    await runtime.flush_outbox()
    assert len(runtime.switch.sent) == 2
    runtime.store.close()
