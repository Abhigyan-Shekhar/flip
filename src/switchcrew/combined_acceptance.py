"""Join existing memory and graph probes without overstating live learning proof."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .company_brain_acceptance import verify_company_brain
from .learning_acceptance import run_two_task_learning
from .memory import memory_from_settings
from .memory_acceptance import verify_shared_memory
from .settings import Settings

_REV = re.compile(r"^[a-fA-F0-9]{40}$")


def check_checkout(settings: Settings) -> dict:
    """Require one exact clean local checkout, not a branch name or stale graph."""
    root = settings.company_brain_repo_root
    revision = settings.company_brain_revision
    if not root or not Path(root).is_absolute() or not _REV.fullmatch(revision):
        return {"status": "BLOCKED", "reason": "absolute_root_and_exact_sha_required"}
    try:
        checkout = Path(root).resolve(strict=True)
        git_root = Path(
            subprocess.check_output(
                ["git", "-C", str(checkout), "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=8,
            ).strip()
        ).resolve(strict=True)
        actual = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return {"status": "BLOCKED", "reason": "git_checkout_unavailable"}
    if git_root != checkout:
        return {"status": "BLOCKED", "reason": "repository_root_mismatch"}
    if actual.lower() != revision.lower() or dirty:
        return {"status": "BLOCKED", "reason": "checkout_revision_or_cleanliness_mismatch"}
    return {"status": "PASS", "root": str(checkout), "revision": actual}


async def run_combined_acceptance(
    settings: Settings,
    *,
    files=None,
    brain_transport=None,
    memory_factory=None,
    learning_probe=None,
) -> dict:
    check = check_checkout(settings)
    if check["status"] != "PASS":
        return {"status": "BLOCKED", "checkout": check, "live_learning": "not_verified"}
    if not settings.memory_enabled or "/acceptance/" not in settings.memory_project:
        return {
            "status": "BLOCKED",
            "checkout": check,
            "reason": "isolated_waggle_acceptance_project_required",
            "live_learning": "not_verified",
        }
    if not files:
        return {
            "status": "BLOCKED",
            "checkout": check,
            "reason": "change_file_required",
            "live_learning": "not_verified",
        }
    try:
        brain = await verify_company_brain(settings, transport=brain_transport, files=files)
    except Exception:
        brain = {"status": "BLOCKED", "reason": "company_brain_probe_error"}
    try:
        factory = memory_factory or (lambda: memory_from_settings(settings))
        memory = await verify_shared_memory(factory)
    except Exception:
        memory = {"status": "BLOCKED", "reason": "waggle_probe_error"}
    try:
        probe = learning_probe or run_two_task_learning
        controlled_learning = await probe()
    except Exception:
        controlled_learning = {"status": "BLOCKED", "reason": "controlled_learning_probe_error"}
    change_count = brain.get("change_source_count")
    passed = (
        all(result.get("status") == "PASS" for result in (brain, memory, controlled_learning))
        and isinstance(change_count, int)
        and change_count > 0
    )
    return {
        "status": "integration_probes_passed" if passed else "BLOCKED",
        "observed_at": datetime.now(UTC).isoformat(),
        "checkout": check,
        "company_brain": brain,
        "waggle_cross_role": memory,
        "controlled_two_task_learning": controlled_learning,
        "live_learning": "not_verified",
        "live_checkout_indexing": "not_verified",
        "limitations": [
            "Company Brain is read-only; operator indexing requires separate evidence.",
            "Two-task learning uses a temporary repo and controlled memory, not live Waggle.",
            "Live lesson approval improving a second external task is still pending.",
        ],
    }


async def main(config_file: str, report_file: str | None = None, files=None) -> int:
    config_path = Path(config_file)
    if config_path.stat().st_mode & 0o077:
        raise ValueError("Acceptance configuration must be private")
    settings = Settings(_env_file=None, **json.loads(config_path.read_text()))
    report = await run_combined_acceptance(settings, files=files)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if report_file:
        descriptor = os.open(report_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write(encoded + "\n")
    print(encoded)
    return 0 if report["status"] == "integration_probes_passed" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--report")
    parser.add_argument("--file", action="append", default=[])
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args.config, args.report, args.file)))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "BLOCKED", "reason": "invalid_private_config"}))
        raise SystemExit(2) from None
