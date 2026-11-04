"""Operator-controlled personal Codex registrations and task authorizations.

This module authorizes an existing local Console Codex adapter. It does not
launch a desktop UI or claim an operating-system sandbox. Host isolation still
depends on each employee's separate Console process/account and OS permissions.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .store import ConflictError, Store, encode

_ACTIVE_TASK_STATES = {"PLANNED", "IN_PROGRESS", "QA_REVIEW", "BLOCKED"}
_SESSION_STATES = {"reserved", "connected", "disconnected", "recovering", "completed", "blocked"}


class EmployeeAuthorizationError(PermissionError):
    """An employee or task is outside its operator-authorized scope."""


def _private_file(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if mode & 0o077:
        raise ValueError(f"{label} must have private permissions")
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _root(path: str | Path) -> Path:
    try:
        value = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("Employee workspace root is unavailable") from exc
    if not value.is_dir():
        raise ValueError("Employee workspace root must be a directory")
    return value


def _contains(root: Path, path: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(path))) == str(root)
    except ValueError:
        return False


class EmployeeDirectory:
    """Bind verified employee actors to distinct Switch and Console identities.

    `registrations` must come from private operator configuration, not task or
    Discord JSON. An employee can only receive work through `assign_task`, and
    a Console session is authorized only for that task's persisted handoff.
    """

    def __init__(
        self,
        store: Store,
        registrations: list[dict[str, Any]],
        *,
        allowed_assigners: set[str] | list[str],
    ):
        if not isinstance(registrations, list) or not registrations:
            raise ValueError("Private employee registrations are required")
        self.store = store
        self.allowed_assigners = frozenset(allowed_assigners)
        if not self.allowed_assigners or any(
            not isinstance(x, str) or not x for x in self.allowed_assigners
        ):
            raise ValueError("At least one trusted employee assignment authority is required")
        self._entries = self._validate_registrations(registrations)
        with store._lock:
            store.connection.executescript("""
            CREATE TABLE IF NOT EXISTS employee_registry(
              employee_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL UNIQUE,
              switch_agent_id TEXT NOT NULL UNIQUE, registration_hash TEXT NOT NULL,
              enabled INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS employee_assignments(
              task_id TEXT PRIMARY KEY REFERENCES tasks(id), employee_id TEXT NOT NULL,
              assigned_by TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS employee_sessions(
              task_id TEXT NOT NULL REFERENCES tasks(id), employee_id TEXT NOT NULL,
              handoff_id TEXT NOT NULL, session_key TEXT NOT NULL UNIQUE,
              console_session_id TEXT, status TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(task_id, employee_id, handoff_id)
            );
            """)
        self._persist_registrations()

    @classmethod
    def from_file(cls, store: Store, path: str | Path, *, allowed_assigners):
        """Load a mode-0600 JSON registration file without logging its contents."""
        config_path = _private_file(Path(path), "Employee configuration")
        try:
            config = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Employee configuration is invalid") from exc
        if not isinstance(config, dict) or not isinstance(config.get("employees"), list):
            raise ValueError("Employee configuration must contain an employees list")
        return cls(store, config["employees"], allowed_assigners=allowed_assigners)

    def _validate_registrations(self, registrations):
        entries = {}
        seen_actor, seen_switch, seen_control, seen_db = set(), set(), set(), set()
        seen_console_id, seen_codex_name = set(), set()
        for raw in registrations:
            required = (
                "employee_id",
                "actor_id",
                "switch_agent_id",
                "codex_switch_agent_id",
                "workspace_root",
                "allowed_roots",
                "control_file",
                "console_agent_id",
                "codex_name",
                "console_database",
            )
            if not isinstance(raw, dict) or any(not raw.get(key) for key in required):
                raise ValueError("Employee registration is missing required operator fields")
            employee_id, actor_id, switch_id = (
                raw["employee_id"],
                raw["actor_id"],
                raw["switch_agent_id"],
            )
            codex_switch_id = raw["codex_switch_agent_id"]
            if any(
                not isinstance(value, str) or not value.strip()
                for value in (employee_id, actor_id, switch_id, codex_switch_id)
            ):
                raise ValueError("Employee, actor, and Switch identities must be non-empty strings")
            console_id, codex_name = raw.get("console_agent_id"), raw.get("codex_name")
            if (
                employee_id in entries
                or actor_id in seen_actor
                or switch_id in seen_switch
                or codex_switch_id in seen_switch
                or console_id in seen_console_id
                or codex_name in seen_codex_name
            ):
                raise ValueError("Employee, actor, Switch, and Console identities must be distinct")
            if codex_switch_id == switch_id:
                raise ValueError("Employee and Console provider Switch identities must differ")
            workspace = _root(raw["workspace_root"])
            roots_raw = raw["allowed_roots"]
            if not isinstance(roots_raw, list) or not roots_raw:
                raise ValueError("At least one approved workspace root is required")
            roots = tuple(sorted({_root(value) for value in roots_raw}, key=str))
            if not any(_contains(root, workspace) for root in roots):
                raise EmployeeAuthorizationError(
                    "Employee workspace is outside its allowlisted roots"
                )
            control_file = _private_file(Path(raw["control_file"]), "Employee Console control file")
            console_database = _private_file(
                Path(raw["console_database"]), "Employee Console database"
            )
            if control_file in seen_control or console_database in seen_db:
                raise ValueError("Employees must use separate Console controls and databases")
            entry = {
                "employee_id": employee_id,
                "actor_id": actor_id,
                "switch_agent_id": switch_id,
                "codex_switch_agent_id": codex_switch_id,
                "workspace_root": workspace,
                "allowed_roots": roots,
                "control_file": control_file,
                "console_agent_id": str(raw["console_agent_id"]),
                "codex_name": str(raw["codex_name"]),
                "console_database": console_database,
                "enabled": raw.get("enabled", True) is True,
            }
            if not entry["console_agent_id"] or not entry["codex_name"]:
                raise ValueError("Dedicated Console Codex identities are required")
            if any(
                _contains(root, sensitive)
                for root in roots
                for sensitive in (control_file, console_database)
            ):
                raise ValueError(
                    "Console credentials and database must stay outside employee workspaces"
                )
            for previous in entries.values():
                if _contains(previous["workspace_root"], workspace) or _contains(
                    workspace, previous["workspace_root"]
                ):
                    raise ValueError("Employee workspace roots must not overlap")
                if any(_contains(root, previous["workspace_root"]) for root in roots) or any(
                    _contains(root, workspace) for root in previous["allowed_roots"]
                ):
                    raise ValueError(
                        "An employee allowlist cannot contain another employee workspace"
                    )
                if any(
                    _contains(root, sensitive)
                    for root in roots
                    for sensitive in (previous["control_file"], previous["console_database"])
                ) or any(
                    _contains(root, sensitive)
                    for root in previous["allowed_roots"]
                    for sensitive in (control_file, console_database)
                ):
                    raise ValueError(
                        "Employee allowlists cannot expose another employee's private files"
                    )
            identity = {
                key: str(value)
                if isinstance(value, Path)
                else [str(x) for x in value]
                if isinstance(value, tuple)
                else value
                for key, value in entry.items()
                if key != "enabled"
            }
            entry["registration_hash"] = hashlib.sha256(encode(identity).encode()).hexdigest()
            entries[employee_id] = entry
            seen_actor.add(actor_id)
            seen_switch.add(switch_id)
            seen_switch.add(codex_switch_id)
            seen_control.add(control_file)
            seen_db.add(console_database)
            seen_console_id.add(console_id)
            seen_codex_name.add(codex_name)
        return entries

    def _persist_registrations(self):
        with self.store._lock, self.store.transaction():
            for employee_id, entry in self._entries.items():
                current = self.store.connection.execute(
                    "SELECT * FROM employee_registry WHERE employee_id=?", (employee_id,)
                ).fetchone()
                if current and any(
                    current[key] != entry[value]
                    for key, value in (
                        ("actor_id", "actor_id"),
                        ("switch_agent_id", "switch_agent_id"),
                        ("registration_hash", "registration_hash"),
                    )
                ):
                    raise ConflictError("Employee registration identity or scope changed")
                self.store.connection.execute(
                    "INSERT INTO employee_registry(employee_id,actor_id,switch_agent_id,"
                    "registration_hash,enabled) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(employee_id) DO UPDATE SET enabled=excluded.enabled,"
                    "updated_at=CURRENT_TIMESTAMP",
                    (
                        employee_id,
                        entry["actor_id"],
                        entry["switch_agent_id"],
                        entry["registration_hash"],
                        int(entry["enabled"]),
                    ),
                )

    def _entry(self, employee_id):
        entry = self._entries.get(employee_id)
        if not entry or not entry["enabled"]:
            raise EmployeeAuthorizationError("Employee registration is unavailable")
        row = self.store.connection.execute(
            "SELECT enabled FROM employee_registry WHERE employee_id=?", (employee_id,)
        ).fetchone()
        if not row or not row["enabled"]:
            raise EmployeeAuthorizationError("Employee registration is disabled")
        return entry

    def assign_task(self, task_id, employee_id, *, assigned_by, expected_version=None):
        """Persist explicit operator assignment without changing immutable task source."""
        if assigned_by not in self.allowed_assigners:
            raise EmployeeAuthorizationError("Trusted task assignment authority is required")
        entry = self._entry(employee_id)
        with self.store._lock, self.store.transaction():
            task = self.store.get_task(task_id)
            if not task:
                raise KeyError(task_id)
            if expected_version is not None and task["version"] != expected_version:
                raise ConflictError("Task version changed before employee assignment")
            if task["status"] not in _ACTIVE_TASK_STATES | {"DRAFT", "AWAITING_APPROVAL"}:
                raise EmployeeAuthorizationError("Only pending or active tasks can be assigned")
            current = self.store.connection.execute(
                "SELECT * FROM employee_assignments WHERE task_id=?", (task_id,)
            ).fetchone()
            if current:
                if current["status"] == "active":
                    if current["employee_id"] != employee_id:
                        raise ConflictError("Task already has a different employee assignment")
                    return dict(current)
                prior_sessions = self.store.connection.execute(
                    "SELECT status FROM employee_sessions WHERE task_id=? AND employee_id=?",
                    (task_id, current["employee_id"]),
                ).fetchall()
                if any(row["status"] not in {"completed", "blocked"} for row in prior_sessions):
                    raise ConflictError("Task already has a different employee assignment")
                self.store.connection.execute(
                    "UPDATE employee_assignments SET employee_id=?,assigned_by=?,status='active',"
                    "updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                    (employee_id, assigned_by, task_id),
                )
            else:
                self.store.connection.execute(
                    "INSERT INTO employee_assignments(task_id,employee_id,assigned_by,status) "
                    "VALUES(?,?,?,'active')",
                    (task_id, employee_id, assigned_by),
                )
            self.store._event(
                task_id,
                "employee.assigned",
                {"employee_id": employee_id, "switch_agent_id": entry["switch_agent_id"]},
                task["version"],
            )
            return dict(
                self.store.connection.execute(
                    "SELECT * FROM employee_assignments WHERE task_id=?", (task_id,)
                ).fetchone()
            )

    def assigned_employee(self, task_id):
        row = self.store.connection.execute(
            "SELECT employee_id FROM employee_assignments WHERE task_id=? AND status='active'",
            (task_id,),
        ).fetchone()
        return row["employee_id"] if row else None

    def revoke_assignment(self, task_id, employee_id, *, revoked_by):
        if revoked_by not in self.allowed_assigners:
            raise EmployeeAuthorizationError("Trusted task assignment authority is required")
        with self.store._lock, self.store.transaction():
            row = self.store.connection.execute(
                "SELECT * FROM employee_assignments WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row or row["employee_id"] != employee_id:
                raise EmployeeAuthorizationError("Active employee assignment was not found")
            if row["status"] == "revoked":
                return dict(row)
            self.store.connection.execute(
                "UPDATE employee_assignments SET status='revoked',updated_at=CURRENT_TIMESTAMP "
                "WHERE task_id=?",
                (task_id,),
            )
            task = self.store.get_task(task_id)
            self.store._event(
                task_id,
                "employee.assignment_revoked",
                {"employee_id": employee_id, "revoked_by": revoked_by},
                task["version"],
            )
            self.store.connection.execute(
                "UPDATE employee_sessions SET status='blocked',updated_at=CURRENT_TIMESTAMP "
                "WHERE task_id=? AND employee_id=? AND status NOT IN ('completed','blocked')",
                (task_id, employee_id),
            )
            return dict(
                self.store.connection.execute(
                    "SELECT * FROM employee_assignments WHERE task_id=?", (task_id,)
                ).fetchone()
            )

    def authorize_assignment(
        self,
        employee_id,
        task_id,
        workspace_root=None,
        *,
        actor_id,
        handoff_id,
    ):
        """Return trusted adapter settings for this addressed task/handoff only."""
        entry = self._entry(employee_id)
        if actor_id != entry["actor_id"]:
            raise EmployeeAuthorizationError("Verified employee actor does not match registration")
        root = _root(workspace_root or entry["workspace_root"])
        if root != entry["workspace_root"] or not any(
            _contains(allowed, root) for allowed in entry["allowed_roots"]
        ):
            raise EmployeeAuthorizationError("Requested workspace is outside employee scope")
        task = self.store.get_task(task_id)
        assignment = self.store.connection.execute(
            "SELECT * FROM employee_assignments "
            "WHERE task_id=? AND employee_id=? AND status='active'",
            (task_id, employee_id),
        ).fetchone()
        handoff = self.store.get_handoff(handoff_id)
        if (
            not task
            or task["status"] not in _ACTIVE_TASK_STATES
            or not assignment
            or not handoff
            or handoff["task_id"] != task_id
            or handoff["recipient"] != entry["switch_agent_id"]
            or handoff["status"] not in {"pending", "accepted"}
        ):
            raise EmployeeAuthorizationError(
                "An active explicit assignment and addressed handoff are required"
            )
        # Match the stable identifier expected by the existing EngineeringExecutor.
        session_key = hashlib.sha256(handoff_id.encode()).hexdigest()[:32]
        with self.store._lock, self.store.transaction():
            old = self.store.connection.execute(
                "SELECT * FROM employee_sessions "
                "WHERE task_id=? AND employee_id=? AND handoff_id=?",
                (task_id, employee_id, handoff_id),
            ).fetchone()
            if old and old["session_key"] != session_key:
                raise ConflictError("Employee task session identity changed")
            self.store.connection.execute(
                "INSERT OR IGNORE INTO employee_sessions "
                "(task_id,employee_id,handoff_id,session_key,status) "
                "VALUES(?,?,?,?, 'reserved')",
                (task_id, employee_id, handoff_id, session_key),
            )
        return {
            "employee_id": employee_id,
            "actor_id": entry["actor_id"],
            "switch_agent_id": entry["switch_agent_id"],
            "codex_switch_agent_id": entry["codex_switch_agent_id"],
            "workspace_root": str(root),
            "session_key": session_key,
            "control_file": str(entry["control_file"]),
            "console_agent_id": entry["console_agent_id"],
            "codex_name": entry["codex_name"],
            "console_database": str(entry["console_database"]),
        }

    def record_session(self, task_id, employee_id, handoff_id, session_id, status):
        if status not in _SESSION_STATES or not isinstance(session_id, str) or not session_id:
            raise ValueError("Valid task session identity and status are required")
        with self.store._lock, self.store.transaction():
            row = self.store.connection.execute(
                "SELECT * FROM employee_sessions "
                "WHERE task_id=? AND employee_id=? AND handoff_id=?",
                (task_id, employee_id, handoff_id),
            ).fetchone()
            if not row:
                raise EmployeeAuthorizationError("Session was not authorized for this task")
            if row["console_session_id"] and row["console_session_id"] != session_id:
                raise ConflictError("Console session changed; operator recovery is required")
            task = self.store.get_task(task_id)
            assignment = self.store.connection.execute(
                "SELECT status FROM employee_assignments WHERE task_id=? AND employee_id=?",
                (task_id, employee_id),
            ).fetchone()
            if (
                not assignment
                or assignment["status"] != "active"
                or not task
                or task["status"] not in _ACTIVE_TASK_STATES
            ):
                status = "blocked"
            self.store.connection.execute(
                "UPDATE employee_sessions SET console_session_id=?,status=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND employee_id=? AND handoff_id=?",
                (session_id, status, task_id, employee_id, handoff_id),
            )
        return self.session_status(task_id, employee_id, handoff_id)

    def session_status(self, task_id, employee_id, handoff_id):
        row = self.store.connection.execute(
            "SELECT task_id,employee_id,handoff_id,session_key,console_session_id,status,"
            "updated_at "
            "FROM employee_sessions WHERE task_id=? AND employee_id=? AND handoff_id=?",
            (task_id, employee_id, handoff_id),
        ).fetchone()
        return dict(row) if row else None

    def set_connection_status(self, employee_id, status):
        if status not in {"connected", "disconnected", "recovering"}:
            raise ValueError("Employee connection status is invalid")
        self._entry(employee_id)
        with self.store._lock, self.store.transaction():
            self.store.connection.execute(
                "UPDATE employee_sessions SET status=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE employee_id=? AND status NOT IN ('completed','blocked')",
                (status, employee_id),
            )
