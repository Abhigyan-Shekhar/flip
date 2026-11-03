"""Read-only, checkout-pinned adapter for the optional Company Brain MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .settings import Settings

MAX_EVIDENCE_CHARS = 8_000
_SECRET = re.compile(
    r"(?i)(?:(?:bearer|basic)\s+\S+|(?:api[_ -]?key|password|secret|token|credential"
    r"|authorization)[\"']?\s*[:=]\s*[\"']?\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)
_RESULT_ARRAY_KEYS = {
    "untrusted_source_excerpts",
    "evidence",
    "results",
    "sources",
    "citations",
    "callers",
    "related",
    "nodes",
}


@dataclass(frozen=True)
class BrainResult:
    """Bounded evidence and any qualification needed to interpret it."""

    context: Any = ""
    warning: str | None = None


class Brain(Protocol):
    async def get_context(self, query: str, max_results: int = 5) -> BrainResult: ...

    async def check_change(
        self, files: list[str], symbols: list[str] | None = None
    ) -> BrainResult: ...


class NullCompanyBrain:
    async def get_context(self, query: str, max_results: int = 5) -> BrainResult:
        return BrainResult()

    async def check_change(self, files: list[str], symbols: list[str] | None = None) -> BrainResult:
        return BrainResult()


Transport = Callable[[str, dict[str, Any]], Awaitable[Any]]


class CompanyBrain:
    """Optional MCP client. It never writes, syncs, refreshes, or trusts another repo."""

    def __init__(
        self,
        *,
        repo_root: str,
        revision: str | None = None,
        command: list[str] | None = None,
        url: str | None = None,
        token: str | None = None,
        timeout: float = 15,
        max_results: int = 5,
        transport: Transport | None = None,
    ) -> None:
        if not os.path.isabs(repo_root):
            raise ValueError("Company Brain repo_root must be an absolute path")
        if transport is None and (bool(command) == bool(url)):
            raise ValueError("Configure exactly one Company Brain command or HTTP endpoint")
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
                raise ValueError(
                    "Company Brain endpoint must be an HTTP(S) URL without credentials"
                )
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote Company Brain endpoints require HTTPS")
        self.repo_root = os.path.realpath(repo_root)
        self.revision = revision.strip() if revision and revision.strip() else None
        self.command, self.url, self._token = list(command or []), url, token
        self.timeout, self._transport = timeout, transport
        self.max_results = min(max(1, max_results), 10)

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._transport is not None:
            return await self._transport(name, arguments)
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        async with AsyncExitStack() as stack:
            if self.url:
                headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
                streams = await stack.enter_async_context(
                    streamablehttp_client(self.url, headers=headers)
                )
            else:
                # Keep application credentials out of the optional server subprocess.
                env = {
                    k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR"}
                }
                errlog = stack.enter_context(open(os.devnull, "w"))
                streams = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=self.command[0], args=self.command[1:], env=env
                        ),
                        errlog=errlog,
                    )
                )
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            return await session.call_tool(name, arguments)

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        async with asyncio.timeout(self.timeout):
            result = await self._invoke(name, arguments)
        if getattr(result, "isError", False):
            raise RuntimeError("tool error")
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return result
        payload = getattr(result, "structuredContent", None)
        if payload is not None:
            return payload
        if result is None or isinstance(result, (str, int, float, bool)):
            return result
        texts = [getattr(item, "text", "") for item in getattr(result, "content", [])]
        if texts:
            try:
                return json.loads(texts[0])
            except (ValueError, TypeError):
                return {"text": "\n".join(texts)}
        return {}

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _sensitive(self, value: Any) -> bool:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
        return bool(_SECRET.search(rendered) or (self._token and self._token in rendered))

    @staticmethod
    def _warnings(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return value
        raise ValueError("Invalid Company Brain warnings")

    @staticmethod
    def _bound_envelope(value: Any, limit: int) -> tuple[Any, bool]:
        """Bound result arrays recursively while preserving the response's other fields."""
        truncated = False
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in _RESULT_ARRAY_KEYS and isinstance(item, list) and len(item) > limit:
                    result[key] = item[:limit]
                    truncated = True
                else:
                    result[key], child_truncated = CompanyBrain._bound_envelope(item, limit)
                    truncated = truncated or child_truncated
            return result, truncated
        if isinstance(value, list):
            mapped = [CompanyBrain._bound_envelope(item, limit) for item in value]
            return [item for item, _ in mapped], any(flag for _, flag in mapped)
        return value, False

    async def _validated(self) -> tuple[dict[str, Any] | None, BrainResult | None]:
        try:
            listed = await self._call("list_repositories", {})
            repos = (
                listed if isinstance(listed, list) else self._dict(listed).get("repositories", [])
            )
            match = next(
                (
                    self._dict(item).get("repository", self._dict(item))
                    for item in repos
                    if os.path.realpath(
                        str(
                            self._dict(self._dict(item).get("repository", item)).get(
                                "repo_root", ""
                            )
                        )
                    )
                    == self.repo_root
                ),
                None,
            )
            if match is None:
                return None, BrainResult(
                    warning="Company Brain has no registered repository for this checkout."
                )
            status = self._dict(
                await self._call("repository_status", {"repo_root": self.repo_root})
            )
            status_root = status.get("repo_root")
            if not status_root or os.path.realpath(str(status_root)) != self.repo_root:
                return None, BrainResult(
                    warning="Company Brain repository status did not match the configured checkout."
                )
            state = str(status.get("status", status.get("state", ""))).lower()
            warnings = self._warnings(status.get("warnings"))
            blocked_warnings = {
                "sync_in_progress",
                "repository_not_indexed",
                "stale_index",
            }.intersection(warnings)
            if state not in {"ok", "incomplete"} or blocked_warnings:
                issue = (
                    ", ".join(sorted(blocked_warnings))
                    if blocked_warnings
                    else (
                        state
                        if state in {"unavailable", "unindexed", "syncing", "stale", "error"}
                        else "unknown"
                    )
                )
                return None, BrainResult(
                    warning=(f"Company Brain index is {issue}; local source remains authoritative.")
                )
            head, dirty = await asyncio.gather(
                self._git("rev-parse", "HEAD"), self._git("status", "--porcelain")
            )
            remote_revision = status.get("local_revision")
            graph_revision = status.get("graph_revision")
            if not head or not remote_revision or not graph_revision or dirty is None:
                return None, BrainResult(warning="Company Brain revision could not be validated.")
            if dirty or status.get("working_tree_dirty") is True:
                return None, BrainResult(
                    warning="Company Brain evidence omitted: checkout has uncommitted changes."
                )
            if head != str(remote_revision) or head != str(graph_revision):
                return None, BrainResult(
                    warning=(
                        "Company Brain evidence omitted: index revision is stale for this checkout."
                    )
                )
            if self.revision and head != self.revision:
                return None, BrainResult(
                    warning=(
                        "Company Brain evidence omitted: checkout differs from configured revision."
                    )
                )
            return status, None
        except Exception:
            return None, BrainResult(
                warning="Company Brain unavailable; use local source as authoritative."
            )

    async def _git(self, *args: str) -> str | None:
        def run() -> str:
            proc = subprocess.run(
                ["git", "-C", self.repo_root, *args],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            return proc.stdout.strip()

        try:
            return await asyncio.to_thread(run)
        except (OSError, subprocess.SubprocessError):
            return None

    async def _retrieve(
        self, tool: str, arguments: dict[str, Any], evidence_limit: int | None = None
    ) -> BrainResult:
        if self._sensitive(arguments):
            return BrainResult(warning="Company Brain query omitted: sensitive content detected.")
        status, problem = await self._validated()
        if problem:
            return problem
        try:
            args = {**arguments, "repo_root": self.repo_root}
            payload = await self._call(tool, args)
            data = self._dict(payload)
            # Reject explicit server claims about a different checkout or revision.
            repository = self._dict(data.get("repository"))
            response_root = data.get("repo_root") or repository.get("repo_root")
            if response_root and os.path.realpath(str(response_root)) != self.repo_root:
                return BrainResult(
                    warning=(
                        "Company Brain returned evidence for a different checkout; "
                        "evidence omitted."
                    )
                )
            evidence_status = str(data.get("status", "")).lower()
            response_warnings = self._warnings(data.get("warnings"))
            if evidence_status in {"unavailable", "unindexed", "syncing", "stale", "error"} or any(
                item in {"sync_in_progress", "repository_not_indexed", "stale_index"}
                for item in response_warnings
            ):
                return BrainResult(
                    warning=("Company Brain evidence unavailable (index not ready).")
                )
            if evidence_status not in {"ok", "incomplete"}:
                return BrainResult(
                    warning=(
                        "Company Brain response status was missing or unknown; evidence omitted."
                    )
                )
            # Preserve the full bounded response envelope: callers, must_preserve,
            # warnings, and source citations can be meaningful beside excerpts.
            evidence, truncated = self._bound_envelope(
                data if data else payload, evidence_limit or self.max_results
            )
            response_revision = data.get("graph_revision")
            if not response_revision:
                return BrainResult(
                    warning=(
                        "Company Brain response did not include a graph revision; evidence omitted."
                    )
                )
            if response_revision != status.get("graph_revision"):
                return BrainResult(
                    warning=(
                        "Company Brain returned evidence from a different graph revision; "
                        "evidence omitted."
                    )
                )
            warnings = self._warnings(data.get("warning")) + response_warnings
            warning = "; ".join(warnings[:10])
            if self._sensitive(warnings):
                return BrainResult(
                    warning="Company Brain evidence omitted: sensitive content detected."
                )
            warning = warning[:500] or None
            if evidence_status == "incomplete":
                warning = (
                    warning or "Company Brain retrieval is incomplete; coverage may be limited."
                )
            context = {
                "repo_root": self.repo_root,
                "revision": status.get("graph_revision") if status else None,
                "evidence": evidence,
                "warnings": response_warnings,
            }
            if status and status.get("status") == "incomplete":
                warning = warning or "Company Brain index is incomplete; coverage may be limited."
            if truncated:
                warning = (
                    warning or "Company Brain result arrays were truncated to the configured limit."
                )
            if self._sensitive(context):
                return BrainResult(
                    warning="Company Brain evidence omitted: sensitive content detected."
                )
            if len(json.dumps(context, ensure_ascii=False, default=str)) > MAX_EVIDENCE_CHARS:
                context = {
                    "repo_root": self.repo_root,
                    "revision": context["revision"],
                    "evidence_excerpt": "",
                    "truncated": True,
                }
                excerpt = json.dumps(evidence, ensure_ascii=False, default=str)
                low, high = 0, min(len(excerpt), MAX_EVIDENCE_CHARS)
                while low < high:
                    middle = (low + high + 1) // 2
                    context["evidence_excerpt"] = excerpt[:middle]
                    if len(json.dumps(context, ensure_ascii=False)) <= MAX_EVIDENCE_CHARS:
                        low = middle
                    else:
                        high = middle - 1
                context["evidence_excerpt"] = excerpt[:low]
                warning = (
                    warning or "Company Brain evidence was truncated to the configured size limit."
                )
            return BrainResult(context=context, warning=str(warning) if warning else None)
        except Exception:
            return BrainResult(
                warning="Company Brain retrieval failed; no graph evidence was used."
            )

    async def get_context(self, query: str, max_results: int = 5) -> BrainResult:
        if not query.strip():
            return BrainResult()
        # The MCP schema has no result-count argument; enforce the size bound locally.
        evidence_limit = min(max(1, max_results), self.max_results)
        return await self._retrieve(
            "get_context", {"query": query[:2000]}, evidence_limit=evidence_limit
        )

    async def check_change(self, files: list[str], symbols: list[str] | None = None) -> BrainResult:
        if not files:
            return BrainResult()
        return await self._retrieve(
            "check_change", {"files": files[:50], "symbols": (symbols or [])[:50]}
        )


def company_brain_from_settings(settings: Settings) -> Brain:
    if not getattr(settings, "company_brain_enabled", False):
        return NullCompanyBrain()
    root = getattr(settings, "company_brain_repo_root", None)
    revision = getattr(settings, "company_brain_revision", "")
    if not root or not revision:
        return _UnavailableCompanyBrain(
            "Company Brain is enabled without an explicit repository root and revision."
        )
    try:
        return CompanyBrain(
            repo_root=str(root),
            revision=revision,
            max_results=getattr(settings, "company_brain_max_results", 5),
            command=getattr(settings, "company_brain_command", None),
            url=getattr(settings, "company_brain_url", None),
            token=(
                settings.company_brain_token.get_secret_value()
                if getattr(settings, "company_brain_token", None)
                else None
            ),
        )
    except ValueError:
        return _UnavailableCompanyBrain(
            "Company Brain configuration is invalid; local source remains authoritative."
        )


class _UnavailableCompanyBrain(NullCompanyBrain):
    def __init__(self, warning: str) -> None:
        self.warning = warning

    async def get_context(self, query: str, max_results: int = 5) -> BrainResult:
        return BrainResult(warning=self.warning)

    async def check_change(self, files: list[str], symbols: list[str] | None = None) -> BrainResult:
        return BrainResult(warning=self.warning)


async def capture_engineering_context(brain: Brain, query: str) -> dict[str, Any]:
    """Capture bounded graph context for an Engineering prompt, failing soft.

    Only explicit file-like references trigger change analysis. Other requests
    use general graph context. Exceptions are reduced to type-only warnings so
    backend details and credentials cannot enter durable task records.
    """
    files = sorted(set(re.findall(r"\b(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,12}\b", query)))[:50]
    try:
        if files:
            result = await brain.check_change(files)
        else:
            result = await brain.get_context(query, max_results=getattr(brain, "max_results", 5))
        return {"context": result.context, "warning": result.warning}
    except Exception as exc:
        return {
            "context": None,
            "warning": f"Company Brain unavailable ({type(exc).__name__})",
        }
