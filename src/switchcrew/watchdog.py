"""Read-only task and worker watchdog with durable, deduplicated findings.

The watchdog observes Store state and an optional Engineering execution ledger.
It never retries a handoff, replays a Console call, or changes workflow status.
Its only workflow-side write is a bounded, idempotent notification in the
existing outbox; recovery remains an operator or owning-agent decision.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .store import Store, encode

log = logging.getLogger("switchcrew.watchdog")


def _time(value, *, assume_utc=False):
    if isinstance(value, datetime):
        current = value
    elif value:
        current = datetime.fromisoformat(value)
    else:
        current = datetime.now(UTC)
    if current.tzinfo is None and assume_utc:
        current = current.replace(tzinfo=UTC)
    if current.tzinfo is None:
        raise ValueError("Watchdog timestamps must include a timezone")
    return current.astimezone(UTC)


def record_worker_heartbeat(store, agent_id, status="connected", *, worker_id=None, at=None):
    """Persist lifecycle state from a trusted runtime process for watchdog polling."""
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("Worker identity is required")
    if status not in {"connected", "disconnected", "recovering", "stopped"}:
        raise ValueError("Worker heartbeat status is invalid")
    stamp = _time(at).isoformat()
    with store._lock:
        store.connection.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_heartbeats(
          agent_id TEXT PRIMARY KEY, status TEXT NOT NULL, worker_id TEXT,
          heartbeat_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)
    with store._lock, store.transaction():
        store.connection.execute(
            "INSERT INTO watchdog_heartbeats(agent_id,status,worker_id,heartbeat_at) "
            "VALUES(?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "status=excluded.status,worker_id=excluded.worker_id,"
            "heartbeat_at=excluded.heartbeat_at,updated_at=CURRENT_TIMESTAMP",
            (agent_id, status, worker_id, stamp),
        )
    return {"agent_id": agent_id, "status": status, "heartbeat_at": stamp}


class Watchdog:
    """Monitor durable work and configured agent heartbeat records."""

    def __init__(self, store: Store, *, config: dict):
        if not isinstance(config, dict):
            raise ValueError("Private watchdog configuration is required")
        monitored = config.get("monitored_agents")
        if not isinstance(monitored, list) or not monitored:
            raise ValueError("At least one expected worker identity must be configured")
        if any(not isinstance(item, str) or not item for item in monitored):
            raise ValueError("Monitored worker identities must be strings")
        if len(set(monitored)) != len(monitored):
            raise ValueError("Monitored worker identities must be unique")
        self.store = store
        self.monitored_agents = frozenset(monitored)
        self.watchdog_id = config.get("watchdog_id", "switchcrew-watchdog")
        self.alert_sender = config.get("alert_sender")
        if self.alert_sender is not None and (
            not isinstance(self.alert_sender, str) or not self.alert_sender
        ):
            raise ValueError("Watchdog alert sender must be a configured Switch identity")
        self.alert_after = int(config.get("alert_after_seconds", 300))
        self.handoff_timeout = int(config.get("handoff_timeout_seconds", 900))
        self.outbox_timeout = int(config.get("outbox_timeout_seconds", 300))
        self.execution_timeout = int(config.get("execution_timeout_seconds", 300))
        self.max_findings = int(config.get("max_findings", 1000))
        self.max_pending_alerts = int(config.get("max_pending_alerts", 100))
        self.execution_ledger = config.get("execution_ledger")
        self.execution_ledger_required = config.get("execution_ledger_required", False) is True
        self._validate_bounds()
        with store._lock:
            store.connection.executescript("""
            CREATE TABLE IF NOT EXISTS watchdog_heartbeats(
              agent_id TEXT PRIMARY KEY, status TEXT NOT NULL, worker_id TEXT,
              heartbeat_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS watchdog_findings(
              fingerprint TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
              task_id TEXT, severity TEXT NOT NULL, status TEXT NOT NULL,
              recovery_state TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL,
              occurrences INTEGER NOT NULL DEFAULT 1, first_seen TEXT NOT NULL,
              last_seen TEXT NOT NULL, resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS watchdog_observations(
              observation_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
              last_seen TEXT NOT NULL, details TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchdog_meta(
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            """)

    def _validate_bounds(self):
        for name in (
            "alert_after",
            "handoff_timeout",
            "outbox_timeout",
            "execution_timeout",
        ):
            value = getattr(self, name)
            if not 1 <= value <= 30 * 24 * 60 * 60:
                raise ValueError(f"Watchdog {name} is outside supported bounds")
        if not 10 <= self.max_findings <= 100_000:
            raise ValueError("Watchdog finding retention bound is invalid")
        if not 1 <= self.max_pending_alerts <= 10_000:
            raise ValueError("Watchdog pending notification bound is invalid")

    def record_heartbeat(self, agent_id, status="connected", *, worker_id=None, at=None):
        """Persist a heartbeat supplied by the authenticated worker runtime."""
        if agent_id not in self.monitored_agents:
            raise PermissionError("Worker identity is not configured for this watchdog")
        return record_worker_heartbeat(self.store, agent_id, status, worker_id=worker_id, at=at)

    def _observe(self, key, now, details):
        row = self.store.connection.execute(
            "SELECT * FROM watchdog_observations WHERE observation_key=?", (key,)
        ).fetchone()
        if row:
            self.store.connection.execute(
                "UPDATE watchdog_observations SET last_seen=?,details=? WHERE observation_key=?",
                (now.isoformat(), encode(details), key),
            )
            return datetime.fromisoformat(row["first_seen"])
        self.store.connection.execute(
            "INSERT INTO watchdog_observations(observation_key,first_seen,last_seen,details) "
            "VALUES(?,?,?,?)",
            (key, now.isoformat(), now.isoformat(), encode(details)),
        )
        return now

    def _finding(self, kind, subject_id, task_id, severity, summary, details, now):
        fingerprint = hashlib.sha256(f"{kind}:{subject_id}".encode()).hexdigest()
        row = self.store.connection.execute(
            "SELECT * FROM watchdog_findings WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        reopened = bool(row and row["status"] == "resolved")
        if row is None:
            count = self.store.connection.execute(
                "SELECT COUNT(*) FROM watchdog_findings"
            ).fetchone()[0]
            if count >= self.max_findings:
                self.store.connection.execute(
                    "DELETE FROM watchdog_findings WHERE fingerprint IN ("
                    "SELECT fingerprint FROM watchdog_findings WHERE status='resolved' "
                    "ORDER BY resolved_at LIMIT ?)",
                    (count - self.max_findings + 1,),
                )
                count = self.store.connection.execute(
                    "SELECT COUNT(*) FROM watchdog_findings"
                ).fetchone()[0]
                if count >= self.max_findings:
                    return None
        if row:
            self.store.connection.execute(
                "UPDATE watchdog_findings SET status='open',recovery_state='operator_review',"
                "summary=?,details=?,severity=?,task_id=?,occurrences=occurrences+1,"
                "last_seen=?,resolved_at=NULL WHERE fingerprint=?",
                (summary, encode(details), severity, task_id, now.isoformat(), fingerprint),
            )
            occurrence = row["occurrences"] + 1
        else:
            self.store.connection.execute(
                "INSERT INTO watchdog_findings(fingerprint,kind,subject_id,task_id,severity,status,"
                "recovery_state,summary,details,first_seen,last_seen) "
                "VALUES(?,?,?,?,?,'open','operator_review',?,?,?,?)",
                (
                    fingerprint,
                    kind,
                    subject_id,
                    task_id,
                    severity,
                    summary,
                    encode(details),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            occurrence = 1
        if (row is None or reopened) and self.alert_sender and task_id:
            task = self.store.get_task(task_id)
            pending_alerts = self.store.connection.execute(
                "SELECT COUNT(*) FROM outbox WHERE status='pending' "
                "AND json_extract(payload,'$.type')='watchdog.alert'"
            ).fetchone()[0]
            if (
                task
                and task["status"] not in {"CANCELLED", "COMPLETED"}
                and pending_alerts < self.max_pending_alerts
            ):
                alert_key = f"watchdog:{fingerprint}:{occurrence}"
                self.store.enqueue_outbox(
                    task_id,
                    "discord",
                    {
                        "type": "watchdog.alert",
                        "text": f"Watchdog {severity.lower()}: {summary}",
                        "worker_sender": self.alert_sender,
                        "finding_id": fingerprint,
                    },
                    alert_key,
                )
        return fingerprint

    def _resolve(self, fingerprint, now, reason):
        row = self.store.connection.execute(
            "SELECT status FROM watchdog_findings WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        if row and row["status"] != "resolved":
            self.store.connection.execute(
                "UPDATE watchdog_findings SET status='resolved',recovery_state=?,"
                "resolved_at=?,last_seen=? WHERE fingerprint=?",
                (reason, now.isoformat(), now.isoformat(), fingerprint),
            )

    def _find_open_handoffs(self, now, emitted):
        rows = self.store.connection.execute(
            "SELECT h.id,h.task_id,h.status,h.recipient,t.status AS task_status "
            "FROM handoffs h JOIN tasks t ON t.id=h.task_id "
            "WHERE h.status IN ('pending','accepted')"
        ).fetchall()
        active = set()
        for row in rows:
            if row["task_status"] in {"CANCELLED", "COMPLETED", "FAILED"}:
                continue
            key = f"handoff:{row['id']}"
            active.add(key)
            first_seen = self._observe(key, now, {"status": row["status"]})
            event_type = "handoff.accepted" if row["status"] == "accepted" else "handoff.created"
            event = None
            for record in self.store.list_events(row["task_id"]):
                if (
                    record["type"] == event_type
                    and record["details"].get("handoff_id") == row["id"]
                ):
                    event = record
            since = (
                _time(event["created_at"], assume_utc=True)
                if event and event.get("created_at")
                else first_seen
            )
            if now - since >= timedelta(seconds=self.handoff_timeout):
                kind = "handoff_timeout"
                summary = (
                    f"Handoff {row['id']} remains {row['status']} beyond its configured timeout."
                )
                fingerprint = self._finding(
                    kind,
                    row["id"],
                    row["task_id"],
                    "Warning",
                    summary,
                    {
                        "handoff_id": row["id"],
                        "status": row["status"],
                        "recipient": row["recipient"],
                    },
                    now,
                )
                if fingerprint:
                    emitted.append(fingerprint)
        existing = self.store.connection.execute(
            "SELECT fingerprint,subject_id FROM watchdog_findings "
            "WHERE kind='handoff_timeout' AND status='open'"
        ).fetchall()
        open_handoffs = {key.removeprefix("handoff:") for key in active}
        for finding in existing:
            if finding["subject_id"] not in open_handoffs:
                self._resolve(finding["fingerprint"], now, "handoff_terminal_or_missing")
        return active

    def _monitor_heartbeats(self, now, emitted):
        for agent in sorted(self.monitored_agents):
            row = self.store.connection.execute(
                "SELECT * FROM watchdog_heartbeats WHERE agent_id=?", (agent,)
            ).fetchone()
            if row:
                last = _time(row["heartbeat_at"])
                if row["status"] == "connected" and now - last < timedelta(
                    seconds=self.alert_after
                ):
                    self._resolve(
                        hashlib.sha256(f"worker_heartbeat:{agent}".encode()).hexdigest(),
                        now,
                        "heartbeat_recovered",
                    )
                    continue
                if row["status"] == "connected":
                    cause = "heartbeat_stale"
                else:
                    cause = f"worker_{row['status']}"
            else:
                key = f"heartbeat:{agent}"
                first = self._observe(key, now, {"agent_id": agent})
                if now - first < timedelta(seconds=self.alert_after):
                    continue
                last, cause = first, "heartbeat_missing"
            task_row = self.store.connection.execute(
                "SELECT t.id FROM tasks t JOIN handoffs h ON h.task_id=t.id "
                "WHERE h.recipient=? AND t.status IN "
                "('PLANNED','IN_PROGRESS','QA_REVIEW','BLOCKED') "
                "ORDER BY t.created_at LIMIT 1",
                (agent,),
            ).fetchone()
            summary = f"Configured worker {agent} is {cause.replace('_', ' ')}."
            fingerprint = self._finding(
                "worker_heartbeat",
                agent,
                task_row["id"] if task_row else None,
                "Warning",
                summary,
                {"agent_id": agent, "condition": cause, "last_heartbeat": last.isoformat()},
                now,
            )
            if fingerprint:
                emitted.append(fingerprint)

    def _monitor_outbox(self, now, emitted):
        pending = self.store.pending_outbox()
        pending_ids = set()
        for item in pending:
            if item["payload"].get("type") == "watchdog.alert":
                # Never recursively alert on the alert channel's own delivery.
                continue
            task = self.store.get_task(item["task_id"])
            if task and task["status"] in {"CANCELLED", "COMPLETED"}:
                continue
            pending_ids.add(item["id"])
            key = f"outbox:{item['id']}"
            first = self._observe(key, now, {"recipient": item["recipient"]})
            if now - first >= timedelta(seconds=self.outbox_timeout):
                fingerprint = self._finding(
                    "pending_outbox",
                    item["id"],
                    item["task_id"],
                    "Warning",
                    f"Outbox delivery {item['id']} remains pending beyond its timeout.",
                    {"outbox_id": item["id"], "recipient": item["recipient"]},
                    now,
                )
                if fingerprint:
                    emitted.append(fingerprint)
        rows = self.store.connection.execute(
            "SELECT fingerprint,subject_id FROM watchdog_findings "
            "WHERE kind='pending_outbox' AND status='open'"
        ).fetchall()
        for row in rows:
            if row["subject_id"] not in pending_ids:
                self._resolve(row["fingerprint"], now, "outbox_sent_or_removed")

    def _monitor_execution_ledger(self, now, emitted):
        if not self.execution_ledger:
            return "unconfigured"
        path = Path(self.execution_ledger).expanduser().resolve()
        if not path.is_file():
            return "unavailable"
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                rows = db.execute("SELECT id,data FROM executions").fetchall()
        except sqlite3.Error:
            return "unavailable"
        for identity, encoded in rows:
            try:
                data = json.loads(encoded)
            except (TypeError, json.JSONDecodeError):
                continue
            phase = data.get("phase")
            if phase not in {"creating_session", "delivering"}:
                self._resolve(
                    hashlib.sha256(f"execution_uncertain:{identity}".encode()).hexdigest(),
                    now,
                    "execution_phase_changed",
                )
                continue
            key = f"execution:{identity}"
            first = self._observe(key, now, {"phase": phase})
            if now - first < timedelta(seconds=self.execution_timeout):
                continue
            fingerprint = self._finding(
                "execution_uncertain",
                identity,
                None,
                "High",
                "Console execution has an uncertain external outcome; do not retry automatically.",
                {"assignment_id": identity, "phase": phase, "action": "operator_reconciliation"},
                now,
            )
            if fingerprint:
                emitted.append(fingerprint)
        return "available"

    def _bound_findings(self):
        count = self.store.connection.execute("SELECT COUNT(*) FROM watchdog_findings").fetchone()[
            0
        ]
        excess = count - self.max_findings
        if excess > 0:
            self.store.connection.execute(
                "DELETE FROM watchdog_findings WHERE fingerprint IN ("
                "SELECT fingerprint FROM watchdog_findings WHERE status='resolved' "
                "ORDER BY resolved_at LIMIT ?)",
                (excess,),
            )
        observation_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM watchdog_observations"
        ).fetchone()[0]
        observation_limit = self.max_findings * 10
        observation_excess = observation_count - observation_limit
        if observation_excess > 0:
            self.store.connection.execute(
                "DELETE FROM watchdog_observations WHERE observation_key IN ("
                "SELECT observation_key FROM watchdog_observations "
                "ORDER BY last_seen LIMIT ?)",
                (observation_excess,),
            )

    def poll_once(self, *, now=None):
        current = _time(now)
        emitted = []
        with self.store._lock, self.store.transaction():
            self._monitor_heartbeats(current, emitted)
            self._find_open_handoffs(current, emitted)
            self._monitor_outbox(current, emitted)
            ledger_status = self._monitor_execution_ledger(current, emitted)
            self._bound_findings()
        return {
            "status": "blocked_configuration"
            if ledger_status in {"unavailable", "unconfigured"} and self.execution_ledger_required
            else "ok",
            "findings": list(dict.fromkeys(emitted)),
            "execution_ledger": ledger_status,
        }

    async def run(self, *, interval_seconds=5):
        if not 1 <= interval_seconds <= 3600:
            raise ValueError("Watchdog polling interval is outside supported bounds")
        while True:
            try:
                self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Keep service running; logs contain type, not private data.
                log.error("watchdog_poll_failed", extra={"error_type": type(exc).__name__})
            await asyncio.sleep(interval_seconds)


async def run_watchdog(config_file):
    config_path = Path(config_file).expanduser().resolve(strict=True)
    if config_path.stat().st_mode & 0o077:
        raise ValueError("Watchdog configuration must have private permissions")
    config = json.loads(config_path.read_text())
    if not isinstance(config, dict) or not config.get("database"):
        raise ValueError("Watchdog config requires the shared SQLite database path")
    store = Store(config["database"])
    watchdog = Watchdog(store, config=config)
    try:
        await watchdog.run(interval_seconds=int(config.get("poll_interval_seconds", 5)))
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    from .logging import configure_logging

    configure_logging()
    try:
        asyncio.run(run_watchdog(args.config))
    except KeyboardInterrupt:
        pass
