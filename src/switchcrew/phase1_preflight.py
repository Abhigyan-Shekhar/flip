"""Read-only Phase 1 dependency probe; never starts workers or authorizes work."""

import argparse
import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import httpx

from .company_brain import company_brain_from_settings
from .engineering import git
from .isolation import DockerIsolation
from .settings import Settings


async def preflight(config):
    """Use a dedicated private config; report blockers without returning credentials."""
    checks = {}

    def record(name, ok, detail):
        checks[name] = {"status": "PASS" if ok else "BLOCKED", "detail": detail}

    try:
        settings = Settings(_env_file=None, **config.get("settings", {}))
    except (ValueError, TypeError):
        return {
            "status": "BLOCKED",
            "checks": {
                "settings": {
                    "status": "BLOCKED",
                    "detail": "Invalid private Settings configuration.",
                }
            },
        }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(str(settings.switch_base_url).rstrip("/") + "/health/ready")
            record("switch", response.status_code == 200, "Actual Switch readiness HTTP response.")
        except httpx.HTTPError:
            record("switch", False, "Switch unavailable; no local orchestration fallback.")
    root = Path(config.get("repo_root", "/nonexistent")).resolve()
    revision = config.get("revision", "")
    try:
        clean = git(root, "status", "--porcelain") == ""
        head = git(root, "rev-parse", "HEAD")
        record(
            "repository",
            clean and head == revision and bool(re.fullmatch("[a-f0-9]{40}", revision)),
            "Checkout must be clean at the configured exact revision.",
        )
    except (OSError, subprocess.SubprocessError):
        record("repository", False, "Configured repository is unavailable.")
    try:
        isolation = DockerIsolation.from_config(config["container"])
        isolation._invoke("image", "inspect", isolation.image)
        isolation._verify_rootless()
        record(
            "isolation",
            config.get("require_isolation") is True,
            "Pinned local image and Docker daemon available; runtime enforces restricted flags.",
        )
    except (KeyError, ValueError, OSError):
        record("isolation", False, "Required Docker isolation or pinned image unavailable.")
    targets = [
        settings.pm_agent_id,
        settings.engineering_agent_id,
        settings.qa_agent_id,
        settings.reviewer_agent_id,
        settings.release_agent_id,
        config.get("security_id"),
        config.get("knowledge_id"),
    ]
    record(
        "identities",
        all(targets) and len(set(targets)) == 7,
        "Seven distinct registered roles are required; worker readiness checked separately.",
    )
    record(
        "discord",
        bool(
            settings.discord_bindings
            and settings.discord_guild_ids
            and settings.discord_user_ids
            and settings.approval_user_ids
            and settings.discord_application_public_key
        ),
        "Verified bindings, human allowlists and signature public key required.",
    )
    record(
        "signed_controls_hosted",
        config.get("signed_controls_hosted") is True,
        "Operator must verify live interaction hosting and original-thread mapping.",
    )
    brain = company_brain_from_settings(settings)
    result = await brain.check_change(["src/switchcrew/security.py"], ["_extract_findings"])
    record(
        "company_brain",
        bool(result.context and result.context.get("revision") == revision),
        result.warning or "Real MCP retrieval at configured revision.",
    )
    repository, base = settings.release_repository, settings.release_base
    record(
        "disposable_base",
        base.startswith("codex/demo-") and base not in {"main", "master"},
        "Only an explicitly configured codex/demo-* target is allowed for this demo.",
    )
    try:
        view = subprocess.run(
            ["gh", "repo", "view", repository, "--json", "viewerPermission"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        permission = json.loads(view.stdout).get("viewerPermission")
        try:
            ref = subprocess.run(
                ["gh", "api", f"repos/{repository}/git/ref/heads/{base}"],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            base_sha = json.loads(ref.stdout).get("object", {}).get("sha")
            record(
                "target_revision",
                base_sha == revision,
                "Disposable remote target must exist at the investigated source revision.",
            )
        except (ValueError, OSError, subprocess.SubprocessError):
            record("target_revision", False, "Disposable remote target is absent or inaccessible.")
        record(
            "github",
            permission in {"WRITE", "MAINTAIN", "ADMIN"},
            "Current gh identity repository access; Release token must be checked separately.",
        )
    except (ValueError, OSError, subprocess.SubprocessError):
        record("github", False, "GitHub repository permissions unavailable.")
    record(
        "merge_checks",
        bool(
            settings.merge_enabled
            and settings.merge_required_checks
            and settings.merge_github_token
        ),
        "Guarded merge must have explicit required GitHub checks and private credentials.",
    )
    record(
        "scanner",
        bool(config.get("scanner_version_verified") and config.get("scanners")),
        "Require actual offline scanner/version/database provenance; fixtures do not count.",
    )
    record(
        "managed_codex",
        config.get("managed_codex_ready") is True,
        "Requires verified managed Console placement; no session may start before approval.",
    )
    record(
        "workers",
        config.get("workers_ready") is True,
        "Operator must verify every required Switch identity has a ready live worker.",
    )
    return {
        "status": "PASS" if all(c["status"] == "PASS" for c in checks.values()) else "BLOCKED",
        "checks": checks,
        "revision": revision,
        "limitation": "Preflight readiness is not a live task, approval, PR or merge receipt.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        parser.error("Configuration must be private (chmod 600).")
    result = asyncio.run(preflight(json.loads(args.config.read_text())))
    if args.report:
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
