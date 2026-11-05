"""Operator-invoked, read-only Company Brain checkout acceptance probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .company_brain import CompanyBrain
from .settings import Settings

_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+"
    r"|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}"
    r"|-----BEGIN .*PRIVATE KEY-----)"
)
Transport = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _source_count(value: Any) -> int:
    """Count source anchors without including their paths or excerpts in the report."""
    count = 0
    if isinstance(value, dict):
        if value.get("source_uid") or value.get("evidence_id") or value.get("citation_id"):
            count += 1
        for child in value.values():
            count += _source_count(child)
    elif isinstance(value, list):
        count += sum(_source_count(child) for child in value)
    return count


def _safe_warning(warning: str | None, token: str | None) -> str | None:
    if not warning:
        return None
    safe = _SECRET.sub("[redacted]", warning)
    if token:
        safe = safe.replace(token, "[redacted]")
    return safe[:400]


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "observed_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "warnings": warnings or [],
        "limitations": "Read-only probe; this does not refresh or write the Company Brain graph.",
    }


async def verify_company_brain(
    settings: Settings,
    *,
    transport: Transport | None = None,
    files: list[str] | None = None,
    query: str = "repository architecture and decisions",
) -> dict[str, Any]:
    """Check configured identity and source-backed retrieval without exposing excerpts."""
    if not settings.company_brain_enabled:
        return _blocked("company_brain_disabled")
    root = settings.company_brain_repo_root
    revision = settings.company_brain_revision.strip()
    if not root or not revision:
        return _blocked("repository_root_or_revision_missing")
    if not os.path.isabs(str(root)):
        return _blocked("repository_root_must_be_absolute")
    requested_files = files or []
    if any(
        not path or Path(path).is_absolute() or ".." in Path(path).parts for path in requested_files
    ):
        return _blocked("change_files_must_be_repository_relative")

    token = (
        settings.company_brain_token.get_secret_value() if settings.company_brain_token else None
    )
    if _SECRET.search(query) or (token and token in query):
        return _blocked("sensitive_probe_query_omitted")
    try:
        brain = CompanyBrain(
            repo_root=str(root),
            revision=revision,
            command=settings.company_brain_command,
            url=settings.company_brain_url,
            token=token,
            max_results=settings.company_brain_max_results,
            transport=transport,
        )
    except ValueError:
        return _blocked("company_brain_configuration_invalid")

    warnings: list[str] = []
    context = await brain.get_context(query, max_results=settings.company_brain_max_results)
    safe_warning = _safe_warning(context.warning, token)
    if safe_warning:
        warnings.append(safe_warning)
    payload = context.context if isinstance(context.context, dict) else {}
    evidence = payload.get("evidence", {})
    source_count = _source_count(evidence)
    returned_root = payload.get("repo_root")
    returned_revision = payload.get("revision")
    identity_match = (
        os.path.realpath(str(returned_root)) == os.path.realpath(str(root))
        and returned_revision == revision
    )
    if not identity_match:
        return {
            **_blocked("retrieval_identity_or_revision_not_confirmed", warnings),
            "repo_root": str(root),
            "configured_revision": revision,
            "returned_revision": returned_revision,
            "identity_match": False,
            "source_count": source_count,
        }
    if source_count < 1:
        return {
            **_blocked("no_source_grounded_evidence_returned", warnings),
            "repo_root": str(root),
            "configured_revision": revision,
            "returned_revision": returned_revision,
            "identity_match": True,
            "source_count": 0,
        }

    change_count: int | None = None
    if requested_files:
        change = await brain.check_change(requested_files)
        safe_warning = _safe_warning(change.warning, token)
        if safe_warning:
            warnings.append(safe_warning)
        change_payload = change.context if isinstance(change.context, dict) else {}
        if (
            os.path.realpath(str(change_payload.get("repo_root", "")))
            != os.path.realpath(str(root))
            or change_payload.get("revision") != revision
        ):
            return {
                **_blocked("change_check_identity_or_revision_not_confirmed", warnings),
                "repo_root": str(root),
                "configured_revision": revision,
                "returned_revision": returned_revision,
                "identity_match": True,
                "source_count": source_count,
            }
        change_count = _source_count(change_payload.get("evidence", {}))
        if change_count < 1:
            return {
                **_blocked("change_check_returned_no_source_anchors", warnings),
                "repo_root": str(root),
                "configured_revision": revision,
                "returned_revision": returned_revision,
                "identity_match": True,
                "source_count": source_count,
                "change_source_count": 0,
            }

    return {
        "status": "PASS",
        "observed_at": datetime.now(UTC).isoformat(),
        "repo_root": str(root),
        "configured_revision": revision,
        "returned_revision": returned_revision,
        "identity_match": True,
        "source_count": source_count,
        "change_source_count": change_count,
        "warnings": warnings,
        "limitations": (
            "Read-only probe; proves this configured MCP path, not deployment-wide availability."
        ),
    }


async def main(
    config_file: str,
    report_file: str | None = None,
    files: list[str] | None = None,
    query: str = "repository architecture and decisions",
) -> int:
    config_path = Path(config_file)
    if config_path.stat().st_mode & 0o077:
        raise ValueError("Company Brain acceptance config must be private")
    settings = Settings(_env_file=None, **json.loads(config_path.read_text()))
    result = await verify_company_brain(settings, files=files, query=query)
    encoded = json.dumps(result, indent=2)
    if report_file:
        descriptor = os.open(report_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write(encoded + "\n")
    print(encoded)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--report")
    parser.add_argument(
        "--query", default="repository architecture and decisions", help="Indexed keyword or symbol"
    )
    parser.add_argument(
        "--file", action="append", default=[], help="Repository-relative file for check_change"
    )
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args.config, args.report, args.file, args.query)))
    except (OSError, ValueError, TypeError):
        print(json.dumps(_blocked("invalid_or_unavailable_private_config")))
        raise SystemExit(1) from None
