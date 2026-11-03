import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from switchcrew.switch import (
    SwitchClient,
    SwitchOwnershipLost,
    SwitchProtocolError,
    parse_sse,
    register_agent,
)


@pytest.mark.asyncio
async def test_sse_multiline_comments_and_partial_frame():
    async def lines():
        for line in [
            ": heartbeat",
            "id: 8",
            "event: message",
            "data: {",
            'data: "x": 1}',
            "",
            "data: incomplete",
        ]:
            yield line

    frames = [frame async for frame in parse_sse(lines())]
    assert frames == [{"event": "message", "id": "8", "data": '{\n"x": 1}'}]


@pytest.mark.asyncio
async def test_http_operation_auth_and_registration():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/ops"):
            return httpx.Response(200, json={"operations": {"send_targeted_message": {}}})
        if request.url.path == "/agents":
            body = json.loads(request.content)
            assert body["integration_profile"]["connection_model"] == "always_on"
            assert body["name"] == "pm-agent"
            assert body["display_name"] == "PM Agent"
            return httpx.Response(200, json={"id": "a", "api_key": "private"})
        return httpx.Response(
            200,
            json={"result": {"event_id": "event", "target_statuses": {"research": "disconnected"}}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://switch.test", "a", "key", "connection", client=http)
        result = await client.send_targeted_message("hello", ["research"], thread_id="thread")
        assert result["target_statuses"]["research"] == "disconnected"
        assert seen[-1].headers["X-Switch-Connection-Id"] == "connection"
        assert seen[-1].headers["Authorization"] == "Bearer key"
        assert json.loads(seen[-1].content)["body"] == "hello"
        await register_agent(
            "https://switch.test",
            "registration",
            "pm-agent",
            "PM",
            display_name="PM Agent",
            client=http,
        )
        assert seen[-1].headers["Authorization"] == "Bearer registration"


@pytest.mark.asyncio
async def test_stream_state_gap_history_and_cursor():
    def handle(request):
        if request.url.path.endswith("/ops"):
            return httpx.Response(200, json={"operations": {"read_context": {}}})
        if request.url.path.endswith("/read_context"):
            return httpx.Response(200, json={"result": {"threads": [], "truncated": False}})
        assert request.headers["Last-Event-ID"] == "99"
        assert request.url.params["rooms"] == "room"
        frames = [
            (
                "connection_state",
                {
                    "connection_id": "c",
                    "agent_id": "a",
                    "rooms": ["room"],
                    "heartbeat_interval_seconds": 2,
                },
            ),
            ("gap", {"from_sequence": 99, "resumed_at": 0, "reason": "restart"}),
            ("message", {"sequence": 1, "room_id": "room", "payload": {"message_id": "m"}}),
        ]
        content = "".join(
            (
                ("id: 1\n" if kind == "message" else "")
                + f"event: {kind}\ndata: {json.dumps(data)}\n\n"
            )
            for kind, data in frames
        )
        return httpx.Response(200, text=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", ["room"], http)
        client.cursor = 99
        stream = client.events()
        assert (await anext(stream))["type"] == "connection_state"
        assert "room" in (await anext(stream))["history"]
        message = await anext(stream)
        assert message["sequence"] == 1 and client.cursor == 0
        await client.acknowledge(1)
        assert client.cursor == 1
        await stream.aclose()


@pytest.mark.asyncio
async def test_sequence_mismatch_is_fatal():
    def handle(request):
        state = {
            "connection_id": "c",
            "agent_id": "a",
            "rooms": [],
            "heartbeat_interval_seconds": 2,
        }
        return httpx.Response(
            200,
            text=(
                f"event: connection_state\ndata: {json.dumps(state)}\n\n"
                'id: 7\nevent: message\ndata: {"sequence":8}\n\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        with pytest.raises(SwitchProtocolError, match="sequence mismatch"):
            await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 409, 500, 503])
async def test_idle_heartbeat_failure_reopens_with_same_id(status):
    opens = []
    beats = []

    class Idle(httpx.AsyncByteStream):
        async def __aiter__(self):
            state = {
                "connection_id": "c",
                "agent_id": "a",
                "rooms": [],
                "generation": len(opens),
                "heartbeat_interval_seconds": 0.001,
            }
            yield f"event: connection_state\ndata: {json.dumps(state)}\n\n".encode()
            await asyncio.Event().wait()

    def handle(request):
        if request.url.path.endswith("/beat"):
            beats.append(json.loads(request.content))
            return httpx.Response(status, json={"detail": {"code": "no_stream"}})
        if opens:
            assert request.url.params["expected_generation"] == "1"
            assert request.headers["Last-Event-ID"] == "42"
        else:
            assert "expected_generation" not in request.url.params
        opens.append(request.url.params["connection_id"])
        return httpx.Response(200, stream=Idle())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        disconnects = []
        client.on_disconnect = lambda: disconnects.append(True)
        stream = client.events()
        await anext(stream)
        await client.acknowledge(42)
        await asyncio.wait_for(anext(stream), 1)
        assert disconnects
        assert opens == ["c", "c"]
        assert beats[0] == {"connection_id": "c", "generation": 1}
        await stream.aclose()


@pytest.mark.asyncio
async def test_fenced_reconnect_stands_down_if_socket_missed_eviction():
    opens = []

    def handle(request):
        if request.url.path.endswith("/beat"):
            return httpx.Response(409, json={"detail": {"code": "no_stream"}})
        opens.append(request)
        if len(opens) == 1:
            state = {
                "connection_id": "c",
                "agent_id": "a",
                "rooms": [],
                "generation": 7,
                "heartbeat_interval_seconds": 10,
            }
            return httpx.Response(
                200, text=f"event: connection_state\ndata: {json.dumps(state)}\n\n"
            )
        assert request.url.params["expected_generation"] == "7"
        return httpx.Response(409, json={"detail": {"code": "taken_over"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        with pytest.raises(SwitchOwnershipLost, match="taken over"):
            await asyncio.wait_for(anext(stream), 1)
        assert len(opens) == 2


@pytest.mark.asyncio
async def test_clean_eof_probes_generation_before_reopening_a_taken_over_stream():
    opens, beats = [], []

    def handle(request):
        if request.url.path.endswith("/beat"):
            beats.append(json.loads(request.content))
            return httpx.Response(409, json={"detail": {"code": "taken_over"}})
        opens.append(request)
        state = {
            "connection_id": "c",
            "agent_id": "a",
            "rooms": [],
            "generation": 7,
            "heartbeat_interval_seconds": 10,
        }
        # Model the server closing a losing stream without delivering eviction.
        return httpx.Response(200, text=f"event: connection_state\ndata: {json.dumps(state)}\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        with pytest.raises(SwitchOwnershipLost, match="taken over"):
            await asyncio.wait_for(anext(stream), 1)
        assert len(opens) == 1
        assert beats == [{"connection_id": "c", "generation": 7}]
        assert not client.connected


@pytest.mark.asyncio
async def test_clean_eof_no_stream_refusal_reconnects_with_cursor_and_fence():
    opens, beats = [], []

    def handle(request):
        if request.url.path.endswith("/beat"):
            beats.append(json.loads(request.content))
            return httpx.Response(409, json={"detail": {"code": "no_stream"}})
        opens.append(request)
        if len(opens) == 2:
            assert request.url.params["expected_generation"] == "7"
            assert request.headers["Last-Event-ID"] == "42"
        state = {
            "connection_id": "c",
            "agent_id": "a",
            "rooms": [],
            "generation": 6 + len(opens),
            "heartbeat_interval_seconds": 10,
        }
        return httpx.Response(200, text=f"event: connection_state\ndata: {json.dumps(state)}\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        await client.acknowledge(42)
        await asyncio.wait_for(anext(stream), 1)
        assert len(opens) == 2
        assert beats == [{"connection_id": "c", "generation": 7}]
        assert client.generation == 8 and client.cursor == 42
        await stream.aclose()


@pytest.mark.asyncio
async def test_eviction_is_terminal_and_does_not_echo_server_prose():
    opens = []

    def handle(request):
        opens.append(request)
        state = {
            "connection_id": "c",
            "agent_id": "a",
            "rooms": [],
            "generation": 1,
            "heartbeat_interval_seconds": 10,
        }
        return httpx.Response(
            200,
            text=(
                f"event: connection_state\ndata: {json.dumps(state)}\n\n"
                'event: evicted\ndata: {"reason":"untrusted secret prose"}\n\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        with pytest.raises(SwitchOwnershipLost) as caught:
            await anext(stream)
        assert "untrusted" not in str(caught.value)
        assert len(opens) == 1


@pytest.mark.asyncio
async def test_heartbeat_lapse_frame_reopens_without_losing_cursor():
    opens = []

    def handle(request):
        opens.append(request)
        state = {
            "connection_id": "c",
            "agent_id": "a",
            "rooms": [],
            "generation": len(opens),
            "heartbeat_interval_seconds": 10,
        }
        content = f"event: connection_state\ndata: {json.dumps(state)}\n\n"
        if len(opens) == 1:
            content += 'event: evicted\ndata: {"code":"heartbeat_lapsed"}\n\n'
        else:
            assert request.url.params["expected_generation"] == "1"
            assert request.headers["Last-Event-ID"] == "42"
        return httpx.Response(200, text=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        await client.acknowledge(42)
        state = await asyncio.wait_for(anext(stream), 1)
        assert state["type"] == "connection_state" and client.generation == 2
        assert client.cursor == 42 and len(opens) == 2
        await stream.aclose()


@pytest.mark.asyncio
async def test_heartbeat_takeover_is_terminal():
    class Idle(httpx.AsyncByteStream):
        async def __aiter__(self):
            state = {
                "connection_id": "c",
                "agent_id": "a",
                "rooms": [],
                "generation": 9,
                "heartbeat_interval_seconds": 0.001,
            }
            yield f"event: connection_state\ndata: {json.dumps(state)}\n\n".encode()
            await asyncio.Event().wait()

    def handle(request):
        if request.url.path.endswith("/beat"):
            return httpx.Response(409, json={"detail": {"code": "taken_over"}})
        return httpx.Response(200, stream=Idle())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        with pytest.raises(SwitchProtocolError, match="taken over"):
            await asyncio.wait_for(anext(stream), 1)


@pytest.mark.asyncio
async def test_history_pagination_converts_epoch_millis_to_iso8601():
    seen = []

    def handle(request):
        if request.url.path.endswith("/ops"):
            return httpx.Response(200, json={"operations": {"read_context": {}}})
        args = json.loads(request.content)
        seen.append(args)
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "threads": [],
                        "truncated": True,
                        "oldest_timestamp": 1704067200000,
                    }
                },
            )
        assert args["before"] == "2024-01-01T00:00:00+00:00"
        return httpx.Response(200, json={"result": {"threads": [], "truncated": False}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", ["room"], http)
        history = await client._recover_history()
    assert len(history["room"]) == 2


@dataclass(frozen=True)
class LiveRecoveryCredentials:
    # Pytest displays fixture arguments on failures, including short tracebacks.
    # Keep the disposable credential out of that representation.
    url: str
    agent_id: str
    api_key: str = field(repr=False)

    def __iter__(self):
        yield self.url
        yield self.agent_id
        yield self.api_key


@pytest.fixture
def live_recovery_credentials():
    """Opt-in only: use a newly registered probe agent, never PM or Research."""
    if os.environ.get("SWITCHCREW_LIVE_RECOVERY") != "1":
        pytest.skip("Requires an isolated real Switch recovery-probe identity")
    try:
        from switchcrew.settings import Settings

        path = Path(os.environ["SWITCHCREW_LIVE_AGENT_FILE"])
        credentials = json.loads(path.read_text())
        if path.stat().st_mode & 0o077:
            pytest.fail("Probe credential file must be private", pytrace=False)
        if not path.name.startswith("recovery-probe"):
            pytest.fail("Use the dedicated recovery-probe credential file", pytrace=False)
        identity, key = credentials["id"], credentials["api_key"]
        if not all(isinstance(value, str) and value for value in (identity, key)):
            raise ValueError
        url = Settings(
            _env_file=None,
            switch_base_url=os.environ.get("SWITCHCREW_LIVE_SWITCH_URL", "http://127.0.0.1:8080"),
        ).switch_base_url
    except (KeyError, OSError, ValueError, TypeError):
        pytest.fail(
            "Configure a valid private isolated probe identity and Switch URL", pytrace=False
        )
    return LiveRecoveryCredentials(url, identity, key)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_heartbeat_expiry_reconnects(live_recovery_credentials):
    class UnbeatingClient(SwitchClient):
        async def _beat(self, interval):
            await asyncio.Event().wait()  # Deliberately miss the probe's real heartbeat TTL.

    url, identity, key = live_recovery_credentials
    client = UnbeatingClient(url, identity, key, connection_id=str(uuid4()))
    stream = client.events()
    try:
        async with asyncio.timeout(15):
            first = await anext(stream)
        generation = client.generation
        async with asyncio.timeout(4 * first["heartbeat_interval_seconds"] + 15):
            resumed = await anext(stream)
        assert resumed["type"] == "connection_state"
        assert resumed["connection_id"] == client.connection_id
        assert client.generation > generation
    finally:
        await stream.aclose()
        await client.close()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_heartbeat_no_stream_and_fenced_reattach(live_recovery_credentials):
    url, identity, key = live_recovery_credentials
    client = SwitchClient(url, identity, key, connection_id=str(uuid4()))
    stream = client.events()
    try:
        async with asyncio.timeout(20):
            state = await anext(stream)
            assert state["type"] == "connection_state"
            generation = client.generation
            assert isinstance(generation, int)
            await stream.aclose()
            # Closing the HTTP stream detaches the real server's stream holder.
            # No server rows or production worker connections are edited.
            while True:
                try:
                    await client._request(
                        "POST",
                        f"/agents/{identity}/connection/beat",
                        json={"connection_id": client.connection_id, "generation": generation},
                    )
                except httpx.HTTPStatusError as exc:
                    assert exc.response.status_code == 409
                    assert exc.response.json()["detail"]["code"] == "no_stream"
                    break
                await asyncio.sleep(0.1)
            stream = client.events()
            state = await anext(stream)
            assert state["connection_id"] == client.connection_id
            assert client.generation > generation
            await client._request(
                "POST",
                f"/agents/{identity}/connection/beat",
                json={"connection_id": client.connection_id, "generation": client.generation},
            )
    finally:
        await stream.aclose()
        await client.close()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_takeover_stops_loser_without_displacing_winner(live_recovery_credentials):
    url, identity, key = live_recovery_credentials
    connection = str(uuid4())
    loser = SwitchClient(url, identity, key, connection_id=connection)
    winner = SwitchClient(url, identity, key, connection_id=connection)
    opens = []

    async def record_open(request):
        if request.url.path.endswith("/events"):
            opens.append(request.url)

    loser.http.event_hooks["request"].append(record_open)
    old_stream, new_stream = loser.events(), winner.events()
    try:
        async with asyncio.timeout(20):
            await anext(old_stream)
            old_generation = loser.generation
            await anext(new_stream)  # Deliberate takeover of this isolated probe only.
            assert winner.generation > old_generation
            refused = await loser.http.post(
                url + f"/agents/{identity}/connection/beat",
                headers={"Authorization": f"Bearer {key}"},
                json={"connection_id": connection, "generation": old_generation},
            )
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "taken_over"
            with pytest.raises(SwitchOwnershipLost):
                await anext(old_stream)
            assert len(opens) == 1
            await winner._request(
                "POST",
                f"/agents/{identity}/connection/beat",
                json={"connection_id": connection, "generation": winner.generation},
            )
    finally:
        await old_stream.aclose()
        await new_stream.aclose()
        await loser.close()
        await winner.close()
