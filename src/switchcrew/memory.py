"""Optional scoped Waggle MCP memory; observation accepts curated durable summaries only."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .settings import Settings

DEFAULT_PROJECT = "github.com/Abhigyan-Shekhar/flip"
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)


@dataclass(frozen=True)
class MemoryResult:
    context: str = ""
    warning: str | None = None
    persisted: bool = False


class Memory(Protocol):
    async def prime(self, role: str, session_id: str) -> MemoryResult: ...

    async def recall(self, query: str, role: str, session_id: str) -> MemoryResult: ...

    async def observe(
        self, user_message: str, assistant_response: str, role: str, session_id: str
    ) -> MemoryResult: ...


class NullMemory:
    async def prime(self, role: str, session_id: str) -> MemoryResult:
        return MemoryResult()

    async def recall(self, query: str, role: str, session_id: str) -> MemoryResult:
        return MemoryResult()

    async def observe(
        self, user_message: str, assistant_response: str, role: str, session_id: str
    ) -> MemoryResult:
        return MemoryResult()


class WaggleMemory:
    def __init__(
        self,
        *,
        project: str = DEFAULT_PROJECT,
        command: list[str] | None = None,
        url: str | None = None,
        token: str | None = None,
        timeout: float = 15,
    ) -> None:
        if bool(command) == bool(url):
            raise ValueError("Configure exactly one Waggle command or HTTP endpoint")
        if not project.strip() or _SECRET.search(project):
            raise ValueError("Waggle project must be a stable identifier without secrets")
        if url:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Waggle endpoint must be an HTTP(S) URL without credentials")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote Waggle endpoints require HTTPS")
        self.project, self.command, self.url = project, list(command or []), url
        self._token, self.timeout = token, timeout

    def _scope(self, role: str, session_id: str) -> dict[str, str]:
        return {"project": self.project, "agent_id": f"switchcrew:{role}", "session_id": session_id}

    def _unsafe(self, text: str) -> bool:
        return bool(_SECRET.search(text) or (self._token and self._token in text))

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        async with AsyncExitStack() as stack:
            if self.url:
                headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
                transport = await stack.enter_async_context(
                    streamablehttp_client(self.url, headers=headers)
                )
            else:
                # Do not inherit Switch/Discord credentials into a memory subprocess.
                env = {
                    k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR"}
                }
                # Stderr can contain provider errors or payloads: never surface it in app logs.
                errlog = stack.enter_context(open(os.devnull, "w"))
                transport = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=self.command[0], args=self.command[1:], env=env
                        ),
                        errlog=errlog,
                    )
                )
            session = await stack.enter_async_context(ClientSession(transport[0], transport[1]))
            await session.initialize()
            return await session.call_tool(name, arguments)

    async def _call(self, name: str, arguments: dict[str, Any]) -> MemoryResult:
        if any(self._unsafe(str(value)) for value in arguments.values()):
            return MemoryResult(warning="Memory skipped: sensitive content detected.")
        try:
            async with asyncio.timeout(self.timeout):
                result = await self._invoke(name, arguments)
            if result.isError:
                return MemoryResult(warning="Memory tool failed; persistence is unconfirmed.")
            payload = result.structuredContent
            texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
            if payload is None and texts:
                try:
                    payload = json.loads(texts[0])
                except (ValueError, TypeError):
                    pass
            if name == "observe_conversation":
                persisted = isinstance(payload, dict) and payload.get("verbatim_stored") is True
                warning = None if persisted else "Memory persistence was not confirmed by Waggle."
                if persisted and payload.get("extraction_errors"):
                    warning = "Memory turn stored; graph extraction reported an error."
                return MemoryResult(warning=warning, persisted=persisted)
            context = (
                json.dumps(payload, ensure_ascii=False) if payload is not None else "\n".join(texts)
            )
            if self._unsafe(context):
                return MemoryResult(warning="Memory context omitted: sensitive content detected.")
            return MemoryResult(context=context[:8000])
        except Exception:
            # Exception strings can contain credentials or server payloads; omit them.
            return MemoryResult(warning="Memory unavailable; persistence is unconfirmed.")

    async def prime(self, role: str, session_id: str) -> MemoryResult:
        return await self._call("prime_context", {"project": self.project})

    async def recall(self, query: str, role: str, session_id: str) -> MemoryResult:
        return await self._call(
            "query_graph",
            {
                "project": self.project,
                "query": query[:4000],
                "max_nodes": 10,
                "max_depth": 1,
                "retrieval_mode": "hybrid",
            },
        )

    async def observe(
        self, user_message: str, assistant_response: str, role: str, session_id: str
    ) -> MemoryResult:
        """Caller opts in by supplying only durable summaries, never raw events or credentials."""
        if not user_message.strip() or not assistant_response.strip():
            return MemoryResult()
        if len(user_message) > 4000 or len(assistant_response) > 4000:
            return MemoryResult(warning="Memory skipped: durable summary exceeds size limit.")
        return await self._call(
            "observe_conversation",
            {
                **self._scope(role, session_id),
                "user_message": user_message,
                "assistant_response": assistant_response,
            },
        )


def memory_from_settings(settings: Settings) -> Memory:
    if not settings.memory_enabled:
        return NullMemory()
    return WaggleMemory(
        project=settings.memory_project,
        command=settings.waggle_command,
        url=settings.waggle_url,
        token=settings.waggle_token.get_secret_value() if settings.waggle_token else None,
    )
