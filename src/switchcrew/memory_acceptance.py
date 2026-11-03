"""Opt-in real Waggle cross-role persistence acceptance; never use production test facts."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .memory import memory_from_settings
from .settings import Settings


async def verify_shared_memory(factory):
    """Fresh adapters/processes must recall one scoped, explicitly synthetic decision."""
    marker = "acceptance-" + uuid4().hex
    producer = factory()
    observation = await producer.observe(
        "Synthetic shared-memory acceptance decision; this is test data.",
        f"For this synthetic acceptance run only, the selected fixture is {marker}.",
        "engineering",
        "producer-" + marker,
    )
    # Recreate the client: recall must come from the server, not in-process state.
    consumer = factory()
    prime = await consumer.prime("documentation", "consumer-" + marker)
    recalled = await consumer.recall(marker, "documentation", "consumer-" + marker)
    warnings = [r.warning for r in (observation, prime, recalled) if r.warning]
    passed = observation.persisted and marker in recalled.context and not warnings
    return {
        "status": "PASS" if passed else "FAIL",
        "observed_at": datetime.now(UTC).isoformat(),
        "project": producer.project,
        "producer_role": "engineering",
        "consumer_role": "documentation",
        "separate_sessions": True,
        "fresh_client": True,
        "verbatim_stored": observation.persisted,
        "recalled_marker": marker in recalled.context,
        "prime_available": not bool(prime.warning),
        "warnings": warnings,
        "limitations": "Proves the configured MCP memory path, not Discord worker deployment.",
    }


async def main(config_file, report_file=None):
    config_path = Path(config_file)
    if config_path.stat().st_mode & 0o077:
        raise ValueError("Memory acceptance config must be private")
    settings = Settings(_env_file=None, **json.loads(config_path.read_text()))
    if not settings.memory_enabled or "/acceptance/" not in settings.memory_project:
        raise ValueError("Enable memory and use a dedicated /acceptance/ project scope")
    result = await verify_shared_memory(lambda: memory_from_settings(settings))
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
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args.config, args.report)))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "FAIL", "reason": "invalid_or_unavailable_private_config"}))
        raise SystemExit(1) from None
