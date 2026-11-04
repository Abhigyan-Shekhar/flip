"""Revision-pinned Git change attribution and documentation drafts."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import signal
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_DIFF_BYTES = 512_000
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)[\"']?\s*[:=]\s*[\"']?\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)


def _safe_metadata(value: Any, *, limit: int = MAX_DIFF_BYTES) -> tuple[Any, bool]:
    """Bound and redact metadata before it enters a task report or draft."""
    changed = False
    if isinstance(value, str):
        safe = _SECRET.sub("[REDACTED]", value)
        safe = safe[:limit]
        return safe, safe != value
    if isinstance(value, list):
        result = []
        for item in value[:1000]:
            safe, altered = _safe_metadata(item, limit=limit)
            result.append(safe)
            changed = changed or altered
        return result, changed or len(value) > 1000
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 1000:
                changed = True
                break
            safe_key, key_changed = _safe_metadata(str(key), limit=300)
            safe_item, item_changed = _safe_metadata(item, limit=limit)
            result[safe_key] = safe_item
            changed = changed or key_changed or item_changed
        return result, changed
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    return _SECRET.sub("[REDACTED]", str(value))[:limit], True


class ChangeReportError(RuntimeError):
    """Git evidence could not be pinned to the requested revisions."""


@dataclass(frozen=True)
class ChangeReport:
    before_revision: str
    after_revision: str
    task_id: str | None
    requester: str | None
    assigned_agent: str | None
    git_author: str
    files: list[dict[str, str]]
    symbols: list[dict[str, Any]]
    code_diff: str
    diff_sha256: str
    diff_truncated: bool
    diff_redacted: bool
    tests: list[str] | None
    reviewer_findings: list[str] | None
    final_outcome: str | None
    source_evidence: dict[str, Any]
    company_brain: dict[str, Any]
    warnings: list[str]
    changelog_draft: str
    pr_summary_draft: str
    documentation_draft: str
    migration_notes_draft: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _git(
    root: str, *args: str, timeout: float = 20, max_bytes: int = MAX_DIFF_BYTES
) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        root,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.defpath, "HOME": os.environ.get("HOME", ""), "LANG": "C.UTF-8"},
        start_new_session=True,
    )
    try:
        stdout, stderr, exceeded = await _bounded_communicate(process, timeout, max_bytes)
    except (TimeoutError, asyncio.CancelledError):
        raise
    if process.returncode:
        if exceeded:
            raise ChangeReportError("Git evidence exceeded the configured output bound")
        raise ChangeReportError("Git evidence command failed")
    if exceeded:
        raise ChangeReportError("Git evidence exceeded the configured output bound")
    return stdout


async def _bounded_communicate(process, timeout: float, limit: int) -> tuple[bytes, bytes, bool]:
    """Drain subprocess pipes with fixed memory, terminating on overflow/timeout/cancel."""
    overflow = asyncio.Event()

    async def read_bounded(stream):
        chunks, size = [], 0
        while chunk := await stream.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                chunks.clear()
                overflow.set()
            elif not overflow.is_set():
                chunks.append(chunk)
        return b"".join(chunks)

    readers = [asyncio.create_task(read_bounded(process.stdout))]
    if process.stderr is not None:
        readers.append(asyncio.create_task(read_bounded(process.stderr)))
    waiter = asyncio.create_task(process.wait())
    overflow_waiter = asyncio.create_task(overflow.wait())
    try:
        done, _ = await asyncio.wait(
            {waiter, overflow_waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        timed_out = not done
        if timed_out or overflow.is_set():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                process.kill()
            await process.wait()
            if timed_out:
                await asyncio.gather(*readers, return_exceptions=True)
                raise TimeoutError
        outputs = await asyncio.gather(*readers)
        stdout = outputs[0]
        stderr = outputs[1] if len(outputs) > 1 else b""
        return stdout, stderr, overflow.is_set()
    except asyncio.CancelledError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        await process.wait()
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    finally:
        overflow_waiter.cancel()
        await asyncio.gather(overflow_waiter, return_exceptions=True)


def _revision(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(char in "0123456789abcdefABCDEF" for char in value)
    )


def _symbols(source: str, path: str) -> dict[str, tuple[str, int]]:
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    result = {}

    def visit(body, scope=()):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = ".".join((*scope, node.name))
                result[name] = (ast.dump(node, include_attributes=False), node.lineno)
                visit(node.body, (*scope, node.name))

    visit(tree.body)
    return result


async def _symbol_changes(
    root: str, before: str, after: str, files: list[dict[str, str]]
) -> list[dict[str, Any]]:
    changes = []
    for entry in files:
        path = entry["path"]
        if not path.endswith(".py"):
            continue
        old, new = {}, {}
        if entry["status"] != "added":
            try:
                raw = await _git(root, "show", f"{before}:{path}")
                old = _symbols(raw.decode("utf-8", errors="replace"), path)
            except ChangeReportError:
                pass
        if entry["status"] != "removed":
            try:
                raw = await _git(root, "show", f"{after}:{path}")
                new = _symbols(raw.decode("utf-8", errors="replace"), path)
            except ChangeReportError:
                pass
        for name in sorted(old.keys() | new.keys()):
            if name not in old:
                state = "added"
            elif name not in new:
                state = "removed"
            elif old[name][0] != new[name][0]:
                state = "modified"
            else:
                continue
            changes.append(
                {
                    "path": path,
                    "symbol": name,
                    "change": state,
                    "before_line": old.get(name, (None, None))[1],
                    "after_line": new.get(name, (None, None))[1],
                }
            )
    return changes


def _file_list(data: bytes) -> list[dict[str, str]]:
    parts = data.decode("utf-8", errors="replace").split("\0")
    result = []
    index = 0
    while index + 1 < len(parts):
        status, path = parts[index], parts[index + 1]
        index += 2
        if not status or not path:
            continue
        change = {"A": "added", "M": "modified", "D": "removed"}.get(status[:1].upper(), "unknown")
        result.append({"status": change, "path": path})
    return result


async def build_change_report(
    repo_root: str | Path,
    before_revision: str,
    after_revision: str,
    *,
    task_id: str | None = None,
    requester: str | None = None,
    assigned_agent: str | None = None,
    tests: list[str] | None = None,
    reviewer_findings: list[str] | None = None,
    final_outcome: str | None = None,
    workflow_evidence: dict[str, Any] | None = None,
    company_brain: Any | None = None,
) -> ChangeReport:
    """Build a draft report from immutable Git revisions and optional Brain evidence."""
    root = str(Path(repo_root).resolve(strict=True))
    if not Path(root).is_dir() or not _revision(before_revision) or not _revision(after_revision):
        raise ValueError("Provide a repository directory and full Git object IDs")
    before = (await _git(root, "rev-parse", f"{before_revision}^{{commit}}")).decode().strip()
    after = (await _git(root, "rev-parse", f"{after_revision}^{{commit}}")).decode().strip()
    if before != before_revision.lower() or after != after_revision.lower():
        raise ChangeReportError("Git did not resolve the exact requested revisions")
    await _git(root, "merge-base", "--is-ancestor", before, after)
    names = await _git(root, "diff", "--name-status", "--no-renames", "-z", before, after)
    files = _file_list(names)
    raw_diff = await _git(
        root, "diff", "--no-ext-diff", "--no-renames", "--unified=3", before, after
    )
    diff_sha = hashlib.sha256(raw_diff).hexdigest()
    raw_text = raw_diff.decode("utf-8", errors="replace")
    code_diff = _SECRET.sub("[REDACTED]", raw_text)
    diff_redacted = code_diff != raw_text
    diff_truncated = False
    log = (await _git(root, "show", "-s", "--format=%an%x00%ae%x00%H", after)).decode().split("\0")
    author = f"{log[0]} <{log[1]}>" if len(log) > 1 else "unknown"
    symbols = await _symbol_changes(root, before, after, files)
    warnings: list[str] = []
    metadata_redacted = False
    try:
        if (
            len(json.dumps(workflow_evidence or {}, ensure_ascii=False, default=str))
            > MAX_DIFF_BYTES
        ):
            raise ValueError("workflow evidence too large")
        workflow_refs, metadata_redacted = _safe_metadata(workflow_evidence or {})
    except (TypeError, ValueError):
        workflow_refs = {}
        warnings.append("Workflow evidence references were unavailable or exceeded the bound.")
    tests, tests_redacted = _safe_metadata(tests) if tests is not None else (None, False)
    reviewer_findings, review_redacted = (
        _safe_metadata(reviewer_findings) if reviewer_findings is not None else (None, False)
    )
    final_outcome, outcome_redacted = (
        _safe_metadata(final_outcome) if final_outcome is not None else (None, False)
    )
    task_id, task_redacted = _safe_metadata(task_id) if task_id is not None else (None, False)
    requester, requester_redacted = (
        _safe_metadata(requester) if requester is not None else (None, False)
    )
    assigned_agent, agent_redacted = (
        _safe_metadata(assigned_agent) if assigned_agent is not None else (None, False)
    )
    if (
        metadata_redacted
        or tests_redacted
        or review_redacted
        or outcome_redacted
        or task_redacted
        or requester_redacted
        or agent_redacted
    ):
        warnings.append("Sensitive or oversized workflow metadata was redacted or bounded.")
    brain_report: dict[str, Any] = {"status": "unknown", "coverage": "unknown"}
    if company_brain is not None:
        try:
            context = await company_brain.get_context(
                f"Change impact for {', '.join(x['path'] for x in files)}"
            )
            impact = await company_brain.check_change(
                [x["path"] for x in files], [x["symbol"] for x in symbols]
            )
            payloads = [context.context, impact.context]
            matching = [
                item
                for item in payloads
                if isinstance(item, dict)
                and item.get("repo_root") == root
                and item.get("revision") == after
            ]
            if len(matching) == len(payloads) and all(
                not item.warning for item in (context, impact)
            ):
                brain_report = {
                    "status": "consistent",
                    "coverage": "available",
                    "context": _safe_metadata(context.context)[0],
                    "impact": _safe_metadata(impact.context)[0],
                    "revision": after,
                }
            else:
                brain_report = {
                    "status": "inconsistent_or_incomplete",
                    "coverage": "unknown",
                    "revision": after,
                }
                warnings.extend(message for message in (context.warning, impact.warning) if message)
        except Exception:
            brain_report = {"status": "unavailable", "coverage": "unknown", "revision": after}
            warnings.append("Company Brain impact could not be verified for the after revision.")
    else:
        warnings.append("Company Brain coverage is unknown; no graph impact evidence was supplied.")

    file_lines = (
        "\n".join(f"- {entry['status']}: `{entry['path']}`" for entry in files)
        or "- No files changed."
    )
    symbol_lines = (
        "\n".join(f"- {entry['change']}: `{entry['path']}::{entry['symbol']}`" for entry in symbols)
        or "- No Python symbols identified or changed."
    )
    attribution = (
        f"Task: {task_id or 'unknown'}; requester: {requester or 'unknown'}; "
        f"assigned agent: {assigned_agent or 'unknown'}; Git author: {author}."
    )
    changelog = (
        f"## Change draft\n\n{attribution}\n\n### Files\n{file_lines}\n\n### Symbols\n"
        f"{symbol_lines}"
    )
    tests_text = ", ".join(tests) if tests is not None else "unknown (not supplied)"
    review_text = (
        "; ".join(reviewer_findings) if reviewer_findings is not None else "unknown (not supplied)"
    )
    pr_summary = (
        f"## Summary draft\n\n{attribution}\n\n"
        f"Changed {len(files)} file(s) across {len(symbols)} detected Python symbol(s).\n\n"
        f"### Verification\n- Tests: {tests_text}\n- Reviewer: {review_text}\n"
        f"- Outcome: {final_outcome or 'unknown'}\n- Diff SHA-256: `{diff_sha}`"
    )
    docs_changed = [
        entry["path"] for entry in files if entry["path"].lower().endswith((".md", ".rst"))
    ]
    docs_draft = (
        "## Documentation draft\n\n"
        + (
            "Documentation files changed: " + ", ".join(docs_changed) + "."
            if docs_changed
            else (
                "No documentation file changed in this revision. Review docs for affected behavior."
            )
        )
        + "\n\nCompany Brain coverage: "
        + str(brain_report["coverage"])
        + "."
    )
    migration_files = [
        entry["path"]
        for entry in files
        if "migration" in entry["path"].lower() or "schema" in entry["path"].lower()
    ]
    migration_draft = (
        "Review these migration-related changes: " + ", ".join(migration_files) + "."
        if migration_files
        else (
            "No migration or schema files were detected; "
            "verify compatibility requirements manually."
        )
    )
    if diff_truncated:
        warnings.append("Git diff exceeded the report bound; full diff is available by SHA-256.")
    if diff_redacted:
        warnings.append("Sensitive-looking values were redacted from the displayed Git diff.")
    return ChangeReport(
        before_revision=before,
        after_revision=after,
        task_id=task_id,
        requester=requester,
        assigned_agent=assigned_agent,
        git_author=author,
        files=files,
        symbols=symbols,
        code_diff=code_diff,
        diff_sha256=diff_sha,
        diff_truncated=diff_truncated,
        diff_redacted=diff_redacted,
        tests=tests,
        reviewer_findings=reviewer_findings,
        final_outcome=final_outcome,
        source_evidence={
            "git_before_revision": before,
            "git_after_revision": after,
            "git_diff_sha256": diff_sha,
            "workflow": workflow_refs,
        },
        company_brain=brain_report,
        warnings=warnings,
        changelog_draft=changelog,
        pr_summary_draft=pr_summary,
        documentation_draft=docs_draft,
        migration_notes_draft=migration_draft,
    )
