"""Offline-safe deployment readiness and honest live-evidence verification.

This tool has no service credentials and never claims a real deployment passed.
An operator must run the live checks on the provisioned verification host.
"""

from __future__ import annotations

import argparse
import json
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from .settings import Settings

REQUIRED_LIVE_CHECKS = (
    "switch_health",
    "discord_original_thread_roundtrip",
    "pm_research_handoff",
    "restart_replay",
    "engineering_approved_execution",
    "independent_qa",
    "independent_review",
    "release_draft_pr",
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _private_file(value: str | Path | None) -> bool:
    if not value:
        return False
    try:
        path = Path(value)
        mode = path.stat().st_mode
        return stat.S_ISREG(mode) and not mode & 0o077
    except OSError:
        return False


def _valid_discord_bindings(settings: Settings) -> bool:
    """Require usable, unambiguous operator-verified Discord ingress configuration."""
    if not (
        settings.discord_guild_ids and settings.discord_user_ids and settings.discord_bindings
    ):
        return False
    guilds, users = set(settings.discord_guild_ids), set(settings.discord_user_ids)
    if any(not isinstance(value, str) or not value.strip() for value in (*guilds, *users)):
        return False
    required = ("room_id", "bridge_id", "guild_id", "channel_id", "participant_id", "user_id")
    seen = set()
    for binding in settings.discord_bindings:
        if not isinstance(binding, dict) or any(
            not isinstance(binding.get(key), str) or not binding[key].strip() for key in required
        ):
            return False
        if (
            binding["room_id"] != settings.switch_room_id
            or binding["guild_id"] not in guilds
            or binding["user_id"] not in users
        ):
            return False
        identity = (binding["room_id"], binding["bridge_id"], binding["participant_id"])
        if identity in seen:
            return False
        seen.add(identity)
    return True


def _secret_value(value) -> str:
    return value.get_secret_value() if value is not None else ""


def inspect_settings(settings: Settings, *, env_file: Path | None = Path(".env")) -> dict:
    """Inspect configuration without connecting to Switch or printing secrets."""
    checks = []

    def add(name: str, ready: bool, detail: str):
        checks.append(Check(name, "ready" if ready else "blocked", detail))

    api_token = _secret_value(settings.api_token)
    pm_key = _secret_value(settings.pm_api_key)
    research_key = _secret_value(settings.research_api_key)
    add("api_auth", bool(api_token), "Independent API bearer token configured")
    identity = (
        settings.pm_agent_id,
        settings.research_agent_id,
        settings.pm_participant_id,
        settings.research_participant_id,
    )
    add(
        "core_switch_identity",
        bool(settings.switch_room_id) and all(identity) and len(set(identity)) == 4,
        "PM/Research agent and participant IDs must be distinct and share one room",
    )
    add(
        "core_switch_credentials",
        bool(pm_key) and bool(research_key) and pm_key != research_key,
        "Independent registered PM and Research API credentials required",
    )
    add(
        "discord_ingress",
        _valid_discord_bindings(settings),
        "Verified Discord bindings must match the room and nonempty guild/user allowlists",
    )
    privileged = (
        settings.engineering_agent_id,
        settings.qa_agent_id,
        settings.reviewer_agent_id,
        settings.release_agent_id,
    )
    add(
        "privileged_identities",
        all(privileged) and len(set(privileged)) == 4 and not set(privileged) & set(identity),
        "Engineering, QA, Reviewer and Release require distinct identities",
    )
    add(
        "approval_operators",
        bool(settings.approval_credentials),
        "At least one separately authenticated human approver is required",
    )
    service_tokens = [
        _secret_value(value)
        for value in (
            settings.api_token,
            settings.pm_api_key,
            settings.research_api_key,
            settings.waggle_token,
            settings.company_brain_token,
        )
        if value is not None
    ]
    approval_tokens = [value.get_secret_value() for value in settings.approval_credentials.values()]
    human_tokens = [value.get_secret_value() for value in settings.human_credentials.values()]
    add(
        "credential_separation",
        len(service_tokens + approval_tokens + human_tokens)
        == len(set(service_tokens + approval_tokens + human_tokens))
        and not (set(settings.approval_credentials) & set(settings.human_credentials)),
        "Service, worker, and human credentials and identities must be independent",
    )
    add(
        "release_target",
        bool(settings.release_repository and settings.release_base),
        "Explicit repository and base branch required for a draft PR",
    )
    if env_file is not None:
        add(
            "env_file_permissions",
            not env_file.is_symlink() and (not env_file.exists() or _private_file(env_file)),
            "Existing .env must be a private regular file",
        )
    for label, path in (
        ("harness_config", settings.harness_config),
        ("organization_config", settings.organization_config),
    ):
        if path is not None:
            add(label, _private_file(path), "Optional operator config must be a private file")
    return {
        "version": 1,
        "status": "ready_for_live_checks"
        if all(c.status == "ready" for c in checks)
        else "blocked_configuration",
        "checks": [asdict(c) for c in checks],
        "live_acceptance": {name: "not_verified" for name in REQUIRED_LIVE_CHECKS},
    }


def verify_evidence_manifest(document: dict) -> dict:
    """Validate references to host-side evidence; this is not independent attestation."""
    if not isinstance(document, dict) or set(document) != {"host", "commit", "checks"}:
        raise ValueError("Evidence manifest requires host, commit and checks")
    if (
        not isinstance(document["host"], str)
        or not document["host"].strip()
        or not isinstance(document["commit"], str)
        or len(document["commit"]) != 40
        or any(c not in "0123456789abcdef" for c in document["commit"].lower())
    ):
        raise ValueError("Host identifier and full git revision required")
    checks = document["checks"]
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_LIVE_CHECKS):
        raise ValueError("All live checks must be represented")
    pending = []
    for name in REQUIRED_LIVE_CHECKS:
        item = checks[name]
        if not isinstance(item, dict) or set(item) != {"status", "receipt"}:
            raise ValueError("Each check needs status and receipt")
        if item["status"] not in {"passed", "failed", "not_run"}:
            raise ValueError("Invalid live check status")
        receipt = item["receipt"]
        if not isinstance(receipt, str) or len(receipt) > 500:
            raise ValueError("Evidence receipt must be a bounded relative reference")
        if receipt.startswith(("/", "~")) or "\\" in receipt or ".." in Path(receipt).parts:
            raise ValueError("Evidence paths must remain within the operator's bundle")
        if item["status"] == "passed" and (
            not receipt.strip() or Path(receipt) == Path(".") or receipt.endswith("/")
        ):
            raise ValueError("A claimed pass requires a receipt file reference")
        if item["status"] != "passed":
            pending.append(name)
    return {
        "status": "operator_claims_complete" if not pending else "live_pending",
        "pending": pending,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Flip deployment prerequisites")
    parser.add_argument("--evidence", type=Path, help="Validate an operator-authored manifest")
    args = parser.parse_args(argv)
    if args.evidence:
        report = verify_evidence_manifest(json.loads(args.evidence.read_text()))
    else:
        report = inspect_settings(Settings())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"ready_for_live_checks", "operator_claims_complete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
