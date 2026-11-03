"""HTTP/SSE adapter for Switch commit 557b62b (agent protocol 6).

Transport delivery is not task acceptance. Call acknowledge only after durable
processing; a failed/uncertain POST is never automatically retried.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx

log = logging.getLogger("switchcrew.switch")


class SwitchProtocolError(RuntimeError):
    pass


class SwitchOwnershipLost(SwitchProtocolError):
    """Another session owns this connection; automatic restart must stand down."""


def _taken_over(response):
    if response.status_code != 409:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    return isinstance(detail, dict) and detail.get("code") == "taken_over"


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[dict]:
    """Parse decoded SSE lines (httpx handles UTF-8 and arbitrary byte chunks)."""
    data: list[str] = []
    event, identity = "message", None
    async for line in lines:
        if line == "":
            if data:
                yield {"event": event, "id": identity, "data": "\n".join(data)}
            data, event, identity = [], "message", None
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
        elif field == "id" and "\x00" not in value:
            identity = value
    # A partial frame at EOF is deliberately discarded, per SSE framing.


class SwitchClient:
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        api_key: str,
        connection_id: str | None = None,
        rooms=(),
        client: httpx.AsyncClient | None = None,
    ):
        self.agent_id = agent_id
        self.connection_id = connection_id or str(uuid4())
        self.rooms = list(rooms)
        self.cursor: int | None = None
        self.generation: int | None = None
        self.connected = False
        self._owned = client is None
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=30)
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.operations: dict | None = None
        # One runtime owns each client. Receipt polling must stop as soon as
        # liveness is lost, including the adapter's internal reconnect backoff.
        self.on_disconnect = None

    def _disconnected(self):
        self.connected = False
        if self.on_disconnect is not None:
            self.on_disconnect()

    async def close(self):
        if self._owned:
            await self.http.aclose()

    async def _request(self, method, path, **kwargs):
        headers = {**self._headers, **kwargs.pop("headers", {})}
        response = await self.http.request(method, self.base_url + path, headers=headers, **kwargs)
        if _taken_over(response):
            raise SwitchOwnershipLost("Connection incarnation taken over")
        response.raise_for_status()
        return response.json()

    async def discover_operations(self):
        result = await self._request("GET", f"/agents/{self.agent_id}/ops")
        self.operations = result["operations"]
        return self.operations

    async def operation(self, name: str, **args):
        if self.operations is None:
            await self.discover_operations()
        if name not in self.operations:
            raise SwitchProtocolError(f"Operation unavailable: {name}")
        response = await self._request(
            "POST",
            f"/agents/{self.agent_id}/ops/{name}",
            headers={"X-Switch-Connection-Id": self.connection_id},
            json=args,
        )
        return response["result"]

    async def connect_to_room(self, room_id):
        return await self.operation("connect_to_room", room_id=room_id)

    async def post_message(self, body, thread_id=None):
        return await self.operation("post_message", body=body, thread_id=thread_id)

    async def send_targeted_message(
        self, body, target_names=None, target_roles=None, thread_id=None
    ):
        if not target_names and not target_roles:
            raise ValueError("At least one name or role target required")
        return await self.operation(
            "send_targeted_message",
            body=body,
            target_names=target_names,
            target_roles=target_roles,
            thread_id=thread_id,
        )

    async def acknowledge(self, sequence: int):
        if self.cursor is None or sequence > self.cursor:
            self.cursor = sequence

    async def _beat(self, interval):
        while True:
            await asyncio.sleep(interval)
            try:
                await self._request(
                    "POST",
                    f"/agents/{self.agent_id}/connection/beat",
                    json={"connection_id": self.connection_id, "generation": self.generation},
                )
            except (TimeoutError, httpx.HTTPError, SwitchOwnershipLost):
                self._disconnected()
                raise

    async def _recover_history(self):
        histories = {}
        for room in self.rooms:
            pages, before = [], None
            while True:
                args = {"room_id": room, "limit": 100}
                if before is not None:
                    args["before"] = datetime.fromtimestamp(float(before) / 1000, UTC).isoformat()
                page = await self.operation("read_context", **args)
                pages.append(page)
                if not page.get("truncated"):
                    break
                oldest = page.get("oldest_timestamp")
                if oldest is None or oldest == before:
                    raise SwitchProtocolError("History pagination did not advance")
                before = oldest
            histories[room] = pages
        return histories

    async def events(self) -> AsyncIterator[dict]:
        """Reconnect reads only; surface gaps with full history for durable replay.

        Consumers must process history before following events. Deduplicate by
        message ID across restarts (sequence alone is not a durable identity).
        Ownership loss is fatal; heartbeat lapse reopens without taking over a
        newer generation. Persisted cursors resume delivery across processes.
        """
        delay = 0.25
        while True:
            beat = None
            try:
                params = {
                    "connection_id": self.connection_id,
                    "scope": "all",
                    "filter": "all",
                    "rooms": ",".join(self.rooms),
                    "protocol": "6",
                    "protocol_accepts": "6",
                    "client": "switchcrew",
                }
                if self.generation is not None:
                    # A reconnect must not silently take a newer holder's stream.
                    params["expected_generation"] = self.generation
                headers = {**self._headers, "Accept": "text/event-stream"}
                if self.cursor is not None:
                    headers["Last-Event-ID"] = str(self.cursor)
                async with self.http.stream(
                    "GET",
                    self.base_url + f"/agents/{self.agent_id}/events",
                    params=params,
                    headers=headers,
                    timeout=None,
                ) as response:
                    response.raise_for_status()
                    first = True
                    stream_ended = False
                    frames = parse_sse(response.aiter_lines()).__aiter__()
                    while True:
                        next_frame = asyncio.create_task(anext(frames))
                        try:
                            waiting = {next_frame, beat} if beat else {next_frame}
                            done, _ = await asyncio.wait(
                                waiting, return_when=asyncio.FIRST_COMPLETED
                            )
                            if beat and beat in done:
                                beat.result()
                            try:
                                frame = next_frame.result()
                            except StopAsyncIteration:
                                stream_ended = True
                                break
                        finally:
                            if not next_frame.done():
                                next_frame.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await next_frame
                        event = json.loads(frame["data"])
                        kind = frame["event"]
                        event.setdefault("type", kind)
                        if first:
                            if kind != "connection_state":
                                raise SwitchProtocolError("Missing connection_state")
                            if (
                                event.get("connection_id") != self.connection_id
                                or event.get("agent_id") != self.agent_id
                            ):
                                raise SwitchProtocolError("Connection identity mismatch")
                            self.rooms = event["rooms"]
                            self.generation = event.get("generation")
                            interval = float(event["heartbeat_interval_seconds"])
                            if interval <= 0:
                                raise SwitchProtocolError("Invalid heartbeat interval")
                            beat = asyncio.create_task(self._beat(interval))
                            self.connected = True
                            first, delay = False, 0.25
                        if kind == "evicted":
                            if event.get("code") == "heartbeat_lapsed":
                                log.warning(
                                    "heartbeat_lapsed",
                                    extra={
                                        "connection_id": self.connection_id,
                                        "generation": self.generation,
                                        "cursor": self.cursor,
                                    },
                                )
                                break
                            raise SwitchOwnershipLost("Connection evicted")
                        if kind == "subscription_changed":
                            self.rooms = event["rooms"]
                        if kind == "gap":
                            event["history"] = await self._recover_history()
                            # Reset only after history has been delivered and consumed.
                            yield event
                            self.cursor = int(event["resumed_at"])
                            continue
                        if frame["id"] is not None:
                            sequence = int(frame["id"])
                            if sequence != event.get("sequence"):
                                raise SwitchProtocolError("SSE and envelope sequence mismatch")
                            if self.cursor is not None and sequence <= self.cursor:
                                continue
                        yield event
                    if stream_ended and self.generation is not None:
                        self._disconnected()
                        # A server may close the displaced stream before its eviction
                        # frame reaches us. Check the old generation before opening
                        # another stream: an ordinary EOF alone is not permission to
                        # reclaim a connection now owned by a newer holder.
                        await self._request(
                            "POST",
                            f"/agents/{self.agent_id}/connection/beat",
                            json={
                                "connection_id": self.connection_id,
                                "generation": self.generation,
                            },
                        )
            except httpx.HTTPStatusError as exc:
                if _taken_over(exc.response):
                    raise SwitchOwnershipLost("Connection incarnation taken over") from exc
                if exc.response.status_code not in (404, 409, 429, 500, 502, 503, 504):
                    raise
                log.warning(
                    "stream_reconnecting",
                    extra={
                        "connection_id": self.connection_id,
                        "generation": self.generation,
                        "cursor": self.cursor,
                        "status_code": exc.response.status_code,
                    },
                )
            except (TimeoutError, httpx.TransportError) as exc:
                log.warning(
                    "stream_reconnecting",
                    extra={
                        "connection_id": self.connection_id,
                        "generation": self.generation,
                        "cursor": self.cursor,
                        "error_type": type(exc).__name__,
                    },
                )
            finally:
                self._disconnected()
                if beat:
                    beat.cancel()
                    with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
                        await beat
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


async def register_agent(
    base_url, registration_token, name, description, *, display_name=None, client=None
):
    """Register one always-on custom worker. Caller stores returned key securely."""
    owned = client is None
    http = client or httpx.AsyncClient(timeout=30)
    try:
        response = await http.post(
            base_url.rstrip("/") + "/agents",
            headers={"Authorization": f"Bearer {registration_token}"},
            json={
                "name": name,
                "description": description,
                "display_name": display_name,
                "connector_type": "switchcrew",
                "integration_profile": {
                    "connection_model": "always_on",
                    "message_exchange": True,
                    "pre_invocation_mediation": [],
                    "post_invocation_mediation": [],
                    "event_reporting": [],
                },
            },
        )
        response.raise_for_status()
        return response.json()
    finally:
        if owned:
            await http.aclose()
