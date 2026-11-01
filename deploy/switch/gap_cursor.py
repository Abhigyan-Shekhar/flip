#!/usr/bin/env python3
"""Reversible application-cursor injection for a stopped test PM; never edits Switch DB."""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

# Pinned Switch treats unknown room-conversation tickets as a replay gap. This
# fits PostgreSQL BIGINT and is outside ordinary ticket allocation in a test run.
# The operator must observe a real gap frame; this is not a retention-expiry test.
MISSING_TICKET = str(2**63 - 1)


def change_cursor(action, database, agent_id, backup):
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT cursor FROM cursors WHERE stream=?", (agent_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Existing worker cursor required")
            current = str(row[0])
            if action == "arm":
                if current == MISSING_TICKET:
                    raise ValueError("Cursor already armed")
                int(current)
                evidence = {
                    "database": str(database),
                    "agent_id": agent_id,
                    "original_cursor": current,
                    "injected_cursor": MISSING_TICKET,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                # Persist rollback evidence before changing state; never overwrite it.
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as output:
                    json.dump(evidence, output)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                target = MISSING_TICKET
            else:
                if backup.stat().st_mode & 0o077:
                    raise ValueError("Backup must be private")
                evidence = json.loads(backup.read_text())
                if evidence["database"] != str(database) or evidence["agent_id"] != agent_id:
                    raise ValueError("Backup does not match database and identity")
                if evidence["injected_cursor"] != MISSING_TICKET or current != MISSING_TICKET:
                    # In particular, never rewind a cursor already advanced by replay.
                    raise ValueError("Cursor changed; refusing to overwrite worker progress")
                target = str(evidence["original_cursor"])
                int(target)
            connection.execute("UPDATE cursors SET cursor=? WHERE stream=?", (target, agent_id))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"action": action, "status": "cursor_changed", "live_gap_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("arm", "restore"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--workers-stopped", action="store_true", required=True)
    args = parser.parse_args()
    try:
        result = change_cursor(args.action, args.database, args.agent_id, args.backup)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"status": "refused", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
