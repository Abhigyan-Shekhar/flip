"""Crash durable SQLite state with transactional event and delivery records."""

import json
import sqlite3
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .models import TaskCreate, TaskStatus


class ConflictError(ValueError):
    """An optimistic version or idempotency key conflicted."""


class InvalidTransitionError(ValueError):
    """The requested state change is not allowed."""


TRANSITIONS = {
    "DRAFT": {"NEEDS_CLARIFICATION", "AWAITING_APPROVAL", "PLANNED"},
    "NEEDS_CLARIFICATION": {"AWAITING_APPROVAL"},
    "AWAITING_APPROVAL": {"PLANNED"},
    "PLANNED": {"IN_PROGRESS"},
    "IN_PROGRESS": {"QA_REVIEW", "COMPLETED"},
    "QA_REVIEW": {"FAILED", "PASSED"},
    "FAILED": {"IN_PROGRESS"},
    "PASSED": {"HUMAN_REVIEW"},
    "HUMAN_REVIEW": {"PUBLISH_APPROVED"},
    "PUBLISH_APPROVED": {"PR_OPEN"},
    "PR_OPEN": {"COMPLETED"},
    "BLOCKED": {"IN_PROGRESS"},
}


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


class Store:
    def __init__(self, path):
        self._lock = RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        existing_version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if existing_version > 1:
            self.connection.close()
            raise RuntimeError("Database schema is newer than this application")
        self.connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, objective TEXT NOT NULL,
          source TEXT NOT NULL, acceptance_criteria TEXT NOT NULL, status TEXT NOT NULL,
          version INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL REFERENCES tasks(id), type TEXT NOT NULL, details TEXT NOT NULL,
          version INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS assignments(id TEXT PRIMARY KEY, task_id TEXT NOT NULL
          REFERENCES tasks(id), agent TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL
          REFERENCES tasks(id), sender TEXT NOT NULL, recipient TEXT NOT NULL,
          question TEXT NOT NULL, acceptance_criteria TEXT NOT NULL, status TEXT NOT NULL,
          result TEXT, version INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS inbox(message_id TEXT NOT NULL, recipient TEXT NOT NULL,
          task_id TEXT REFERENCES tasks(id), payload TEXT NOT NULL, status TEXT NOT NULL,
          PRIMARY KEY(message_id,recipient));
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, task_id TEXT NOT NULL
          REFERENCES tasks(id), recipient TEXT NOT NULL, payload TEXT NOT NULL,
          idempotency_key TEXT UNIQUE NOT NULL, status TEXT NOT NULL, external_id TEXT);
        CREATE TABLE IF NOT EXISTS cursors(stream TEXT PRIMARY KEY, cursor TEXT NOT NULL);
        INSERT OR IGNORE INTO schema_migrations VALUES(1);
        PRAGMA user_version=1;
        COMMIT;
        """)

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _row(self, row):
        if row is None:
            return None
        value = dict(row)
        for key in ("source", "acceptance_criteria", "details", "payload", "result"):
            if key in value and value[key] is not None:
                value[key] = json.loads(value[key])
        return value

    def _event(self, task_id, kind, details, version):
        self.connection.execute(
            "INSERT INTO events(task_id,type,details,version) VALUES(?,?,?,?)",
            (task_id, kind, encode(details), version),
        )

    def create_task(self, objective, source, acceptance_criteria, task_id=None):
        data = TaskCreate(
            objective=objective, source=source, acceptance_criteria=acceptance_criteria
        )
        task_id = task_id or str(uuid4())
        with self.transaction():
            existing = self.get_task(task_id)
            if existing:
                if any(
                    existing[k] != getattr(data, k)
                    for k in ("objective", "source", "acceptance_criteria")
                ):
                    raise ConflictError("Task ID already used with different content")
                return existing
            self.connection.execute(
                "INSERT INTO tasks(id,objective,source,acceptance_criteria,status,version) "
                "VALUES(?,?,?,?,?,0)",
                (
                    task_id,
                    data.objective,
                    encode(data.source),
                    encode(data.acceptance_criteria),
                    "DRAFT",
                ),
            )
            self._event(task_id, "task.created", {}, 0)
        return self.get_task(task_id)

    def get_task(self, task_id):
        return self._row(
            self.connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        )

    def list_events(self, task_id):
        return [
            self._row(r)
            for r in self.connection.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,)
            )
        ]

    def _check(self, task_id, expected_version):
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["version"] != expected_version:
            raise ConflictError("Task version changed")
        return task

    def transition_task(self, task_id, status, expected_version, details=None):
        status = TaskStatus(str(status).upper()).value
        with self.transaction():
            task = self._check(task_id, expected_version)
            allowed = set(TRANSITIONS.get(task["status"], set()))
            if task["status"] not in {"COMPLETED", "CANCELLED"}:
                allowed.update({"BLOCKED", "CANCELLED", "FAILED"})
            if status == task["status"] or status not in allowed:
                raise InvalidTransitionError(f"Cannot transition {task['status']} to {status}")
            version = expected_version + 1
            self.connection.execute(
                "UPDATE tasks SET status=?,version=? WHERE id=?", (status, version, task_id)
            )
            self._event(
                task_id,
                "task.transitioned",
                {"from": task["status"], "to": status, **(details or {})},
                version,
            )
            self._enqueue(
                task_id,
                "events",
                {"type": "task.transitioned", "status": status, "version": version},
                f"{task_id}:version:{version}",
            )
        return self.get_task(task_id)

    def create_handoff(
        self,
        task_id,
        sender,
        recipient,
        question,
        acceptance_criteria,
        expected_version,
        handoff_id=None,
    ):
        if not all(isinstance(v, str) and v.strip() for v in (sender, recipient, question)):
            raise ValueError("Sender, recipient and question are required")
        handoff_id = handoff_id or str(uuid4())
        with self.transaction():
            existing = self.get_handoff(handoff_id)
            if existing:
                requested = dict(
                    task_id=task_id,
                    sender=sender,
                    recipient=recipient,
                    question=question,
                    acceptance_criteria=acceptance_criteria,
                )
                if any(existing[key] != value for key, value in requested.items()):
                    raise ConflictError("Handoff ID reused with different content")
                return existing
            self._check(task_id, expected_version)
            self.connection.execute(
                "INSERT INTO handoffs(id,task_id,sender,recipient,question,"
                "acceptance_criteria,status) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    handoff_id,
                    task_id,
                    sender,
                    recipient,
                    question,
                    encode(acceptance_criteria),
                    "pending",
                ),
            )
            self.connection.execute(
                "INSERT INTO assignments(id,task_id,agent,status) VALUES(?,?,?,?)",
                (handoff_id, task_id, recipient, "assigned"),
            )
            self.connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))
            self._event(
                task_id,
                "handoff.created",
                {"handoff_id": handoff_id, "sender": sender, "recipient": recipient},
                expected_version + 1,
            )
            self._enqueue(
                task_id,
                recipient,
                {
                    "type": "handoff",
                    "handoff_id": handoff_id,
                    "question": question,
                    "acceptance_criteria": acceptance_criteria,
                },
                handoff_id,
            )
        return self.get_handoff(handoff_id)

    def get_handoff(self, handoff_id):
        return self._row(
            self.connection.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
        )

    def _handoff_update(self, handoff_id, sender, status, result=None, expected_version=None):
        with self.transaction():
            handoff = self.get_handoff(handoff_id)
            if handoff is None:
                raise KeyError(handoff_id)
            if sender != handoff["recipient"]:
                raise PermissionError("Only the addressed recipient may act on a handoff")
            if expected_version is not None and handoff["version"] != expected_version:
                raise ConflictError("Handoff version changed")
            if handoff["status"] == status:
                if status == "completed" and handoff["result"] != result:
                    raise ConflictError("Completed result differs")
                return handoff
            if (handoff["status"], status) not in {
                ("pending", "accepted"),
                ("accepted", "completed"),
            }:
                raise InvalidTransitionError("Handoff must be accepted before completion")
            self.connection.execute(
                "UPDATE handoffs SET status=?,result=?,version=version+1 WHERE id=?",
                (status, encode(result) if result is not None else None, handoff_id),
            )
            self.connection.execute(
                "UPDATE assignments SET status=? WHERE id=?", (status, handoff_id)
            )
            task = self.get_task(handoff["task_id"])
            self._event(
                task["id"],
                f"handoff.{status}",
                {"handoff_id": handoff_id, "sender": sender, "result": result},
                task["version"],
            )
            self._enqueue(
                task["id"],
                handoff["sender"],
                {"type": f"handoff.{status}", "handoff_id": handoff_id, "result": result},
                f"{handoff_id}:{status}",
            )
        return self.get_handoff(handoff_id)

    def accept_handoff(self, handoff_id, sender, expected_version=None):
        return self._handoff_update(
            handoff_id, sender, "accepted", expected_version=expected_version
        )

    def complete_handoff(self, handoff_id, sender, result):
        if not isinstance(result, dict):
            raise ValueError("Handoff result must be an object")
        return self._handoff_update(handoff_id, sender, "completed", result=result)

    def receive_inbox(self, message_id, recipient, payload, task_id=None):
        with self.transaction():
            old = self.connection.execute(
                "SELECT * FROM inbox WHERE message_id=? AND recipient=?", (message_id, recipient)
            ).fetchone()
            if old:
                if old["payload"] != encode(payload):
                    raise ConflictError("Message ID reused with different payload")
                return False
            self.connection.execute(
                "INSERT INTO inbox VALUES(?,?,?,?,?)",
                (message_id, recipient, task_id, encode(payload), "pending"),
            )
        return True

    def pending_inbox(self, recipient):
        return [
            self._row(r)
            for r in self.connection.execute(
                "SELECT * FROM inbox WHERE recipient=? AND status='pending' ORDER BY rowid",
                (recipient,),
            )
        ]

    def complete_inbox(self, message_id, recipient):
        self.connection.execute(
            "UPDATE inbox SET status='completed' WHERE message_id=? AND recipient=?",
            (message_id, recipient),
        )

    def _enqueue(self, task_id, recipient, payload, key):
        old = self.connection.execute(
            "SELECT * FROM outbox WHERE idempotency_key=?", (key,)
        ).fetchone()
        if old:
            if (
                old["payload"] != encode(payload)
                or old["recipient"] != recipient
                or old["task_id"] != task_id
            ):
                raise ConflictError("Outbox key reused with different content")
            return self._row(old)
        item_id = str(uuid4())
        self.connection.execute(
            "INSERT INTO outbox VALUES(?,?,?,?,?,?,NULL)",
            (item_id, task_id, recipient, encode(payload), key, "pending"),
        )
        return self._row(
            self.connection.execute("SELECT * FROM outbox WHERE id=?", (item_id,)).fetchone()
        )

    def enqueue_outbox(self, task_id, recipient, payload, idempotency_key=None):
        with self.transaction():
            return self._enqueue(task_id, recipient, payload, idempotency_key or str(uuid4()))

    def pending_outbox(self):
        return [
            self._row(r)
            for r in self.connection.execute(
                "SELECT * FROM outbox WHERE status='pending' ORDER BY rowid"
            )
        ]

    def mark_outbox_sent(self, item_id, external_id=None):
        self.connection.execute(
            "UPDATE outbox SET status='sent',external_id=? WHERE id=?", (external_id, item_id)
        )

    def get_cursor(self, stream):
        row = self.connection.execute(
            "SELECT cursor FROM cursors WHERE stream=?", (stream,)
        ).fetchone()
        return row[0] if row else None

    def set_cursor(self, stream, cursor):
        self.connection.execute(
            "INSERT INTO cursors VALUES(?,?) ON CONFLICT(stream) "
            "DO UPDATE SET cursor=excluded.cursor",
            (stream, str(cursor)),
        )

    def record_assignment(self, task_id, agent, status="assigned"):
        assignment_id = str(uuid4())
        self.connection.execute(
            "INSERT INTO assignments VALUES(?,?,?,?)", (assignment_id, task_id, agent, status)
        )
        return assignment_id

    def finish_inbox(self, message_id, recipient, outgoing=None):
        """Atomically checkpoint processing and queue its effects.

        outgoing items contain task_id, recipient, payload and idempotency_key.
        External execution must itself honor the persisted idempotency key.
        """
        with self.transaction():
            row = self.connection.execute(
                "SELECT status FROM inbox WHERE message_id=? AND recipient=?",
                (message_id, recipient),
            ).fetchone()
            if row is None:
                raise KeyError(message_id)
            if row["status"] == "completed":
                return False
            for item in outgoing or []:
                self._enqueue(
                    item["task_id"], item["recipient"], item["payload"], item["idempotency_key"]
                )
            self.connection.execute(
                "UPDATE inbox SET status='completed' WHERE message_id=? AND recipient=?",
                (message_id, recipient),
            )
        return True

    def healthcheck(self):
        return self.connection.execute("SELECT 1").fetchone()[0] == 1


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


# One connection is shared by FastAPI threads; retain the lock across complete
# public operations, including nested reads inside transactions.
for _name, _method in list(vars(Store).items()):
    if callable(_method) and not _name.startswith("_") and _name != "transaction":
        setattr(Store, _name, _serialized(_method))
