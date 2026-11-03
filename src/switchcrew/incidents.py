"""Deterministic, durable intake for verified Discord incident reports.

This module records reports only. It never starts workers or performs external I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from uuid import uuid4

from .discord import DiscordSource
from .store import encode

_FAILURE = re.compile(
    (
        "\\b(crash(?:es|ed|ing)?|error(?:s)?|exception|broken|fail(?:s|ed|ure|ing)?|ou"
        "tage|down|unavailable|timeout|timed out|not working|doesn't "
        "work|cannot|can't|bug|regression|vulnerability|exploit|security "
        "issue|breach|leak)\\b"
    ),
    re.I,
)
_SECURITY = re.compile(
    r"\b(vulnerability|exploit|security issue|breach|leak|injection|auth bypass)\b", re.I
)
_SEVERITY = (
    (
        "critical",
        re.compile(
            r"\b(all users|production down|complete outage|data breach|actively exploited)\b", re.I
        ),
    ),
    (
        "high",
        re.compile(
            r"\b(production outage|data loss|security vulnerability|multiple users|widespread)\b",
            re.I,
        ),
    ),
    ("medium", re.compile(r"\b(error|crash|failure|bug|regression|timeout|unavailable)\b", re.I)),
)
_COMPONENTS = {
    "payments": r"\b(payment|payments|billing|checkout|coupon)\b",
    "api": r"\bapi\b",
    "database": r"\b(database|db|sqlite|postgres)\b",
    "discord": r"\bdiscord\b",
    "auth": r"\b(auth|login|permission)\b",
    "ui": r"\b(ui|frontend|button|page)\b",
}


def _utc(value):
    if isinstance(value, datetime):
        return value.timestamp()
    if value is None:
        return time.time()
    return float(value)


def _source_dict(src):
    if is_dataclass(src):
        return asdict(src)
    if isinstance(src, dict):
        return dict(src)
    raise ValueError("Expected a normalized DiscordSource")


def _source_ref(source):
    return {
        "guild_id": source["guild_id"],
        "channel_id": source["channel_id"],
        "message_id": source["message_id"],
    }


class IncidentIntake:
    def __init__(
        self,
        store,
        allowed_channels,
        *,
        clock=time.time,
        max_events_per_minute=10,
        dedupe_window_seconds=3600,
    ):
        self.store = store
        self.allowed_channels = frozenset(str(x) for x in allowed_channels)
        self.clock = clock
        self.max_events_per_minute = int(max_events_per_minute)
        self.dedupe_window_seconds = int(dedupe_window_seconds)
        if self.max_events_per_minute < 1 or self.dedupe_window_seconds < 0:
            raise ValueError("Invalid intake limits")
        with store._lock, store.transaction():
            for statement in (
                (
                    "CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, fingerprint TEXT "
                    "NOT NULL, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, room_id TEXT "
                    "NOT NULL, thread_id TEXT, classification TEXT NOT NULL, severity TEXT NOT "
                    "NULL, confidence TEXT NOT NULL, hypothesis TEXT NOT NULL, original_request "
                    "TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0, task_id TEXT NOT NULL "
                    "UNIQUE REFERENCES tasks(id), created_at REAL NOT NULL, updated_at REAL NOT "
                    "NULL)"
                ),
                (
                    "CREATE INDEX IF NOT EXISTS incident_dedupe ON "
                    "incidents(guild_id,channel_id,room_id,fingerprint,created_at)"
                ),
                (
                    "CREATE TABLE IF NOT EXISTS incident_sources(guild_id TEXT NOT NULL, "
                    "channel_id TEXT NOT NULL, message_id TEXT NOT NULL, incident_id TEXT NOT "
                    "NULL REFERENCES incidents(id), source TEXT NOT NULL, received_at REAL NOT "
                    "NULL, PRIMARY KEY(guild_id,channel_id,message_id))"
                ),
                (
                    "CREATE TABLE IF NOT EXISTS incident_rate(channel_id TEXT NOT NULL, user_id "
                    "TEXT NOT NULL, window_start INTEGER NOT NULL, count INTEGER NOT NULL, "
                    "PRIMARY KEY(channel_id,user_id))"
                ),
                (
                    "CREATE TABLE IF NOT EXISTS incident_seen(guild_id TEXT NOT NULL, "
                    "channel_id TEXT NOT NULL, message_id TEXT NOT NULL, incident_id TEXT NOT "
                    "NULL REFERENCES incidents(id), PRIMARY KEY(guild_id,channel_id,message_id))"
                ),
            ):
                store.connection.execute(statement)

    def ingest(self, source):
        d = _source_dict(source)
        required = (
            "guild_id",
            "channel_id",
            "user_id",
            "room_id",
            "participant_id",
            "message_id",
            "body",
        )
        if any(not isinstance(d.get(k), str) or not d[k].strip() for k in required):
            return None
        if d["channel_id"] not in self.allowed_channels or len(d["body"]) > 10000:
            return None
        if d.get("thread_id") is not None and not isinstance(d["thread_id"], str):
            return None
        if isinstance(source, DiscordSource) is False and not isinstance(source, dict):
            return None
        body = d["body"].strip()
        actionable = bool(_FAILURE.search(body) or _SECURITY.search(body))
        if not actionable:
            # Follow-up chatter is accepted only in an already subscribed incident thread.
            if not d.get("thread_id"):
                return None
        classification = (
            "security_report"
            if _SECURITY.search(body)
            else (
                "service_failure"
                if re.search(r"\b(outage|down|unavailable|timeout|timed out)\b", body, re.I)
                else "bug_report"
            )
        )
        severity = next((name for name, rx in _SEVERITY if rx.search(body)), "low")
        component = next(
            (name for name, pat in _COMPONENTS.items() if re.search(pat, body, re.I)), "general"
        )
        category = (
            "security"
            if classification == "security_report"
            else ("service" if classification == "service_failure" else "bug")
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", body.lower())
        # Fingerprint based on evidence tokens, removing generic report language.
        ignored = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "when",
            "while",
            "for",
            "some",
            "users",
            "please",
            "help",
            "bug",
            "error",
            "fails",
            "failed",
            "fail",
            "failure",
            "broken",
            "issue",
            "report",
            "production",
        }
        token_forms = {
            "failing": "fail",
            "failed": "fail",
            "fails": "fail",
            "failures": "fail",
            "payments": "payment",
        }
        canonical_tokens = {token_forms.get(x, x) for x in normalized.split() if len(x) > 2}
        tokens = sorted(canonical_tokens - ignored)
        fingerprint = hashlib.sha256(encode([component, category, tokens]).encode()).hexdigest()
        now = _utc(self.clock())
        source_doc = {k: d.get(k) for k in (*required, "thread_id", "roles")}
        source_doc["body"] = body
        synthetic = bool(re.search(r"\b(synthetic|fixture|test incident)\b", body, re.I))
        with self.store._lock, self.store.transaction():
            conn = self.store.connection
            seen = conn.execute(
                (
                    "SELECT incident_id FROM incident_seen WHERE guild_id=? AND channel_id=? "
                    "AND message_id=?"
                ),
                (d["guild_id"], d["channel_id"], d["message_id"]),
            ).fetchone()
            if seen:
                item = self.get(seen["incident_id"])
                return {
                    "task_id": item["task_id"],
                    "incident_id": item["id"],
                    "created": False,
                    "incident": item,
                }
            window = int(now // 60)
            # Enforce both per-user fairness and a channel-wide admission ceiling.
            for rate_user in (d["user_id"], "*"):
                rate = conn.execute(
                    "SELECT window_start,count FROM incident_rate WHERE channel_id=? AND user_id=?",
                    (d["channel_id"], rate_user),
                ).fetchone()
                if (
                    rate
                    and rate["window_start"] == window
                    and rate["count"] >= self.max_events_per_minute
                ):
                    return None
            for rate_user in (d["user_id"], "*"):
                rate = conn.execute(
                    "SELECT window_start,count FROM incident_rate WHERE channel_id=? AND user_id=?",
                    (d["channel_id"], rate_user),
                ).fetchone()
                if rate and rate["window_start"] == window:
                    conn.execute(
                        "UPDATE incident_rate SET count=count+1 WHERE channel_id=? AND user_id=?",
                        (d["channel_id"], rate_user),
                    )
                else:
                    conn.execute(
                        (
                            "INSERT INTO incident_rate VALUES(?,?,?,1) "
                            "ON CONFLICT(channel_id,user_id) "
                            "DO UPDATE SET window_start=excluded.window_start,count=1"
                        ),
                        (d["channel_id"], rate_user, window),
                    )
            match = None
            if d.get("thread_id"):
                match = conn.execute(
                    """SELECT i.* FROM incidents i JOIN tasks t ON t.id=i.task_id
                  WHERE i.guild_id=? AND i.channel_id=? AND i.room_id=? AND i.thread_id=?
                  AND t.status NOT IN ('CANCELLED','COMPLETED','FAILED','BLOCKED')
                  ORDER BY i.created_at DESC LIMIT 1""",
                    (d["guild_id"], d["channel_id"], d["room_id"], d["thread_id"]),
                ).fetchone()
            if match is None and actionable:
                match = conn.execute(
                    """SELECT i.* FROM incidents i JOIN tasks t ON t.id=i.task_id
                  WHERE i.guild_id=? AND i.channel_id=? AND i.room_id=? AND i.fingerprint=?
                  AND i.created_at>=? AND t.status NOT IN (
                    'CANCELLED','COMPLETED','FAILED','BLOCKED')
                  ORDER BY i.created_at DESC LIMIT 1""",
                    (
                        d["guild_id"],
                        d["channel_id"],
                        d["room_id"],
                        fingerprint,
                        now - self.dedupe_window_seconds,
                    ),
                ).fetchone()
            if match:
                incident_id, task_id = match["id"], match["task_id"]
                conn.execute(
                    "INSERT INTO incident_seen VALUES(?,?,?,?)",
                    (d["guild_id"], d["channel_id"], d["message_id"], incident_id),
                )
                count = conn.execute(
                    "SELECT COUNT(*) FROM incident_sources WHERE incident_id=?",
                    (incident_id,),
                ).fetchone()[0]
                if count < 100:
                    conn.execute(
                        "INSERT INTO incident_sources VALUES(?,?,?,?,?,?)",
                        (
                            d["guild_id"],
                            d["channel_id"],
                            d["message_id"],
                            incident_id,
                            encode(source_doc),
                            now,
                        ),
                    )
                conn.execute(
                    "UPDATE incidents SET updated_at=?, thread_id=COALESCE(thread_id,?) WHERE id=?",
                    (now, d.get("thread_id") or d["message_id"], incident_id),
                )
                self.store._event(
                    task_id,
                    "incident.source_added",
                    {"incident_id": incident_id, "source_ref": _source_ref(source_doc)},
                    self.store.get_task(task_id)["version"],
                )
                result = self.get(incident_id)
                return {
                    "task_id": task_id,
                    "incident_id": incident_id,
                    "created": False,
                    "incident": result,
                }
            if not actionable:
                return None
            incident_id, task_id = str(uuid4()), str(uuid4())
            subscription_thread = d.get("thread_id") or d["message_id"]
            source_record = {
                "kind": "discord",
                "incident_id": incident_id,
                **{
                    k: d.get(k)
                    for k in (
                        "room_id",
                        "message_id",
                        "user_id",
                        "channel_id",
                        "guild_id",
                    )
                },
                "thread_id": subscription_thread,
                "source": source_doc,
            }
            criteria = [
                "Investigate the reported incident using recorded source evidence.",
                "Propose a minimal fix only when engineering action is justified.",
                "Record findings and verification; do not infer approval from the report.",
            ]
            task = self.store.create_task(
                f"Investigate reported {category}: {body[:500]}",
                source_record,
                criteria,
                task_id=task_id,
            )
            conn.execute(
                "INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    incident_id,
                    fingerprint,
                    d["guild_id"],
                    d["channel_id"],
                    d["room_id"],
                    subscription_thread,
                    classification,
                    severity,
                    "unconfirmed",
                    f"Possible {component} {category}; unconfirmed until investigated.",
                    body,
                    int(synthetic),
                    task_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO incident_sources VALUES(?,?,?,?,?,?)",
                (
                    d["guild_id"],
                    d["channel_id"],
                    d["message_id"],
                    incident_id,
                    encode(source_doc),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO incident_seen VALUES(?,?,?,?)",
                (d["guild_id"], d["channel_id"], d["message_id"], incident_id),
            )
            self.store._event(
                task_id,
                "incident.created",
                {
                    "incident_id": incident_id,
                    "classification": classification,
                    "severity": severity,
                    "confidence": "unconfirmed",
                    "source": source_doc,
                },
                task["version"],
            )
            return {
                "task_id": task_id,
                "incident_id": incident_id,
                "created": True,
                "incident": self.get(incident_id),
            }

    def get(self, incident_id):
        row = self.store.connection.execute(
            "SELECT * FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
        if not row:
            # Accept task IDs for the requested get(taskid) convenience.
            row = self.store.connection.execute(
                "SELECT * FROM incidents WHERE task_id=?", (incident_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["sources"] = [
            json.loads(x[0])
            for x in self.store.connection.execute(
                (
                    "SELECT source FROM incident_sources WHERE incident_id=? ORDER BY "
                    "received_at,rowid LIMIT 100"
                ),
                (row["id"],),
            )
        ]
        result["task"] = self.store.get_task(row["task_id"])
        result["category"] = {
            "security_report": "security",
            "bug_report": "bug",
            "service_failure": "service_failure",
        }[result["classification"]]
        result["severity"] = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV3"}[
            result["severity"]
        ]
        result["summary"] = result["original_request"][:1000]
        result["needs_engineering"] = result["category"] in {"bug", "service_failure"}
        result["evidence_refs"] = [
            f"discord:{x['guild_id']}:{x['channel_id']}:{x['message_id']}"[:120]
            for x in result["sources"][:20]
        ]
        return result
