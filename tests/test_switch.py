import asyncio
import json

import httpx
import pytest

from switchcrew.switch import SwitchClient, SwitchProtocolError, parse_sse, register_agent


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
        await register_agent("https://switch.test", "registration", "pm", "PM", client=http)
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
async def test_idle_heartbeat_failure_reopens_with_same_id():
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
            return httpx.Response(409, json={"detail": {"code": "no_stream"}})
        opens.append(request.url.params["connection_id"])
        return httpx.Response(200, stream=Idle())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SwitchClient("https://s.test", "a", "key", "c", client=http)
        stream = client.events()
        await anext(stream)
        await asyncio.wait_for(anext(stream), 1)
        assert opens == ["c", "c"]
        assert beats[0] == {"connection_id": "c", "generation": 1}
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
