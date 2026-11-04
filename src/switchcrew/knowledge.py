"""Approved, provenance-preserving ingestion of external project notes.

The SQLite store is the access-control authority. Imported content is always
untrusted source material and cannot assign its own owner, scope, or approver.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_DOCUMENT_BYTES = 1_000_000
MAX_RETRIEVAL_CHARS = 12_000
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token|credential|authorization)"
    r"[\"']?\s*[:=]\s*[\"']?\S+|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)"
    r"[A-Za-z0-9_.-]{12,}|-----BEGIN .*PRIVATE KEY-----)"
)
_QUERY_TOKEN = re.compile(r"[\w.-]{2,}", re.UNICODE)
_SCOPES = {"personal", "team", "org", "project"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("source_timestamp must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("source_timestamp must include a timezone")
    return parsed.astimezone(UTC).isoformat()


class KnowledgeService:
    """Import and retrieve approved notes using operator supplied actor ACLs.

    `trusted_actors` is configuration supplied by the host, never extracted
    from imported files. Example entries use `projects`, `teams`, `orgs`, and
    `approve_projects`, `approve_teams`, `approve_orgs` lists.
    """

    def __init__(
        self,
        store,
        *,
        trusted_actors: dict[str, dict[str, Any]],
        import_roots: list[str | Path],
        max_document_bytes: int = MAX_DOCUMENT_BYTES,
    ) -> None:
        if not trusted_actors:
            raise ValueError("At least one operator configured actor is required")
        if not import_roots:
            raise ValueError("At least one external-note import root is required")
        if type(max_document_bytes) is not int or not 1 <= max_document_bytes <= MAX_DOCUMENT_BYTES:
            raise ValueError("max_document_bytes must be from 1 through 1,000,000")
        self.store = store
        self.trusted_actors = {
            actor: {
                key: tuple(values) for key, values in profile.items() if isinstance(values, list)
            }
            for actor, profile in trusted_actors.items()
            if isinstance(actor, str) and actor.strip() and isinstance(profile, dict)
        }
        self.import_roots = tuple(Path(root).resolve(strict=True) for root in import_roots)
        self.max_document_bytes = max_document_bytes
        with store._lock:
            store.connection.execute(
                """CREATE TABLE IF NOT EXISTS external_knowledge (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    team_id TEXT,
                    org_id TEXT,
                    source_path TEXT NOT NULL,
                    source_owner TEXT NOT NULL,
                    source_timestamp TEXT NOT NULL,
                    imported_by TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    related_task_id TEXT,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    approved_by TEXT,
                    approved_at TEXT,
                    verification_status TEXT NOT NULL,
                    UNIQUE(project_id, owner_id, scope, scope_id, content_sha256)
                )"""
            )
            store.connection.execute(
                "CREATE INDEX IF NOT EXISTS external_knowledge_project_status "
                "ON external_knowledge(project_id, approval_status, scope)"
            )

    def _actor(self, actor_id: str) -> dict[str, Any]:
        if not isinstance(actor_id, str) or actor_id not in self.trusted_actors:
            raise PermissionError("Actor is not operator-authorized")
        return self.trusted_actors[actor_id]

    @staticmethod
    def _has(profile: dict[str, Any], key: str, value: str) -> bool:
        return value in profile.get(key, ())

    def _request_scope(
        self, actor_id: str, project_id: str, scope: str, scope_id: str | None
    ) -> tuple[str, str | None, str | None]:
        profile = self._actor(actor_id)
        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or not self._has(profile, "projects", project_id)
        ):
            raise PermissionError("Actor is not a member of this project")
        if scope not in _SCOPES:
            raise ValueError("scope must be personal, team, org, or project")
        if scope == "personal":
            if scope_id not in (None, actor_id):
                raise PermissionError("Personal scope is bound to the authenticated owner")
            return actor_id, None, None
        if scope == "project":
            if scope_id not in (None, project_id):
                raise PermissionError("Project scope must match project_id")
            return project_id, None, None
        if scope == "team":
            if not scope_id or not self._has(profile, "teams", scope_id):
                raise PermissionError("Actor is not a member of this team")
            return scope_id, scope_id, None
        if not scope_id or not self._has(profile, "orgs", scope_id):
            raise PermissionError("Actor is not a member of this organization")
        return scope_id, None, scope_id

    def _resolve_source(self, source: str | Path) -> tuple[Path, Path, str]:
        candidate = Path(source).expanduser()
        if ".." in candidate.parts:
            raise ValueError("Source path traversal is not allowed")
        attempts = (
            [candidate]
            if candidate.is_absolute()
            else [root / candidate for root in self.import_roots]
        )
        for path in attempts:
            try:
                absolute = path.absolute()
            except OSError:
                continue
            for root in self.import_roots:
                try:
                    lexical_relative = absolute.relative_to(root)
                except ValueError:
                    continue
                if not lexical_relative.parts:
                    continue
                cursor = root
                has_symlink = False
                for part in lexical_relative.parts:
                    cursor = cursor / part
                    if cursor.is_symlink():
                        has_symlink = True
                        break
                if has_symlink:
                    raise ValueError("Symlinked source files are not allowed")
                try:
                    resolved = path.resolve(strict=True)
                except OSError:
                    continue
                if resolved == root or root not in resolved.parents:
                    continue
                relative = resolved.relative_to(root)
                if not resolved.is_file() or resolved.suffix.lower() not in {
                    ".md",
                    ".txt",
                    ".json",
                }:
                    raise ValueError("Only regular Markdown, text, or JSON files can be imported")
                return root, resolved, relative.as_posix()
        raise ValueError("Source must be inside a configured import root")

    @staticmethod
    def _validate_json_secrets(value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if re.search(
                    r"(?i)(secret|password|token|credential|authorization|api[_-]?key)", str(key)
                ):
                    return True
                if KnowledgeService._validate_json_secrets(item):
                    return True
        elif isinstance(value, list):
            return any(KnowledgeService._validate_json_secrets(item) for item in value)
        elif isinstance(value, str):
            return bool(_SECRET.search(value))
        return False

    def _read_content(self, path: Path) -> tuple[str, str]:
        if path.stat().st_size > self.max_document_bytes:
            raise ValueError("Source file is empty or exceeds the configured size limit")
        raw = path.read_bytes()
        if not raw or len(raw) > self.max_document_bytes:
            raise ValueError("Source file is empty or exceeds the configured size limit")
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Source file must be UTF-8 text") from exc
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError("JSON source is invalid") from exc
            if self._validate_json_secrets(parsed):
                raise ValueError("Source contains sensitive data")
        if _SECRET.search(content):
            raise ValueError("Source contains sensitive data")
        return content, hashlib.sha256(raw).hexdigest()

    def ingest_file(
        self,
        source: str | Path,
        *,
        actor_id: str,
        project_id: str,
        scope: str = "personal",
        scope_id: str | None = None,
        source_owner: str,
        source_timestamp: str | None = None,
        related_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture a bounded file snapshot as pending, unverified project knowledge."""
        if (
            not isinstance(source_owner, str)
            or not source_owner.strip()
            or len(source_owner) > 200
            or _SECRET.search(source_owner)
        ):
            raise ValueError("source_owner must be a bounded non-empty provenance label")
        actual_scope_id, team_id, org_id = self._request_scope(
            actor_id, project_id, scope, scope_id
        )
        root, path, relative = self._resolve_source(source)
        content, digest = self._read_content(path)
        timestamp = (
            _validate_timestamp(source_timestamp)
            if source_timestamp
            else datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        )
        if related_task_id is not None:
            task = self.store.get_task(related_task_id)
            if task is None:
                raise ValueError("related_task_id must refer to an existing task")
            task_project = task.get("source", {}).get("project_id")
            if task_project is not None and task_project != project_id:
                raise PermissionError("Related task belongs to another project")
        identity = (
            "doc-"
            + hashlib.sha256(
                f"{project_id}\0{actor_id}\0{scope}\0{actual_scope_id}\0{digest}".encode()
            ).hexdigest()[:32]
        )
        with self.store._lock, self.store.transaction():
            self.store.connection.execute(
                """INSERT OR IGNORE INTO external_knowledge
                (id, project_id, owner_id, scope, scope_id, team_id, org_id,
                 source_path, source_owner, source_timestamp, imported_by, imported_at,
                 related_task_id, content, content_sha256, approval_status, approved_by,
                 approved_at, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL,
                        'snapshot_hash_captured')""",
                (
                    identity,
                    project_id,
                    actor_id,
                    scope,
                    actual_scope_id,
                    team_id,
                    org_id,
                    f"{root}:{relative}",
                    source_owner.strip(),
                    timestamp,
                    actor_id,
                    _now(),
                    related_task_id,
                    content,
                    digest,
                ),
            )
            row = self.store.connection.execute(
                "SELECT * FROM external_knowledge WHERE id=?", (identity,)
            ).fetchone()
        return self._document(row)

    @staticmethod
    def _document(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        return value

    def _can_approve(self, actor_id: str, row: dict[str, Any]) -> bool:
        profile = self._actor(actor_id)
        if row["scope"] == "personal":
            return actor_id == row["owner_id"] or self._has(
                profile, "approve_personal", row["owner_id"]
            )
        if row["scope"] == "team":
            return self._has(profile, "approve_teams", row["scope_id"])
        if row["scope"] == "org":
            return self._has(profile, "approve_orgs", row["scope_id"])
        return self._has(profile, "approve_projects", row["project_id"])

    def approve_document(self, document_id: str, *, actor_id: str) -> dict[str, Any]:
        """Explicitly approve distribution; hash integrity does not certify truth."""
        with self.store._lock, self.store.transaction():
            row = self.store.connection.execute(
                "SELECT * FROM external_knowledge WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError("External knowledge record not found")
            value = dict(row)
            if not self._can_approve(actor_id, value):
                raise PermissionError("Actor cannot approve this knowledge scope")
            if hashlib.sha256(value["content"].encode()).hexdigest() != value["content_sha256"]:
                raise ValueError("Stored content hash no longer verifies")
            if value["approval_status"] == "rejected":
                raise ValueError("Rejected knowledge cannot be approved")
            self.store.connection.execute(
                """UPDATE external_knowledge SET approval_status='approved', approved_by=?,
                   approved_at=?, verification_status='snapshot_hash_verified' WHERE id=?""",
                (actor_id, _now(), document_id),
            )
            row = self.store.connection.execute(
                "SELECT * FROM external_knowledge WHERE id=?", (document_id,)
            ).fetchone()
        return self._document(row)

    def reject_document(self, document_id: str, *, actor_id: str) -> dict[str, Any]:
        with self.store._lock, self.store.transaction():
            row = self.store.connection.execute(
                "SELECT * FROM external_knowledge WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError("External knowledge record not found")
            value = dict(row)
            if not self._can_approve(actor_id, value):
                raise PermissionError("Actor cannot reject this knowledge scope")
            if value["approval_status"] == "approved":
                raise ValueError("Approved knowledge requires an explicit revocation workflow")
            self.store.connection.execute(
                "UPDATE external_knowledge SET approval_status='rejected', approved_by=?, "
                "approved_at=? WHERE id=?",
                (actor_id, _now(), document_id),
            )
            row = self.store.connection.execute(
                "SELECT * FROM external_knowledge WHERE id=?", (document_id,)
            ).fetchone()
        return self._document(row)

    def _can_read(self, actor_id: str, row: dict[str, Any]) -> bool:
        profile = self._actor(actor_id)
        if row["scope"] == "personal":
            return actor_id == row["owner_id"]
        if row["scope"] == "team":
            return self._has(profile, "teams", row["team_id"] or "")
        if row["scope"] == "org":
            return self._has(profile, "orgs", row["org_id"] or "")
        return self._has(profile, "projects", row["project_id"])

    def retrieve(
        self,
        query: str,
        *,
        actor_id: str,
        project_id: str,
        related_task_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Return only approved, hash-verified notes visible to the authenticated actor."""
        profile = self._actor(actor_id)
        if not self._has(profile, "projects", project_id):
            raise PermissionError("Actor is not a member of this project")
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("query must be bounded text")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be from 1 through 20")
        if _SECRET.search(query):
            return {"status": "omitted_sensitive_query", "documents": []}
        with self.store._lock:
            rows = self.store.connection.execute(
                "SELECT * FROM external_knowledge "
                "WHERE project_id=? AND approval_status='approved'",
                (project_id,),
            ).fetchall()
        terms = {token.casefold() for token in _QUERY_TOKEN.findall(query)}
        candidates = []
        for raw in rows:
            row = dict(raw)
            if not self._can_read(actor_id, row):
                continue
            if related_task_id is not None and row["related_task_id"] not in (
                None,
                related_task_id,
            ):
                continue
            if hashlib.sha256(row["content"].encode()).hexdigest() != row["content_sha256"]:
                continue
            body = row["content"]
            overlap = len(terms & {t.casefold() for t in _QUERY_TOKEN.findall(body)})
            if terms and overlap == 0:
                continue
            row["content"] = body[:4000]
            row["content_truncated"] = len(body) > 4000
            candidates.append((overlap, row))
        candidates.sort(key=lambda item: (-item[0], item[1]["imported_at"], item[1]["id"]))
        documents = []
        used = 0
        for _, row in candidates:
            item = {
                key: row[key]
                for key in (
                    "id",
                    "scope",
                    "scope_id",
                    "owner_id",
                    "source_path",
                    "source_owner",
                    "source_timestamp",
                    "imported_by",
                    "imported_at",
                    "related_task_id",
                    "content_sha256",
                    "verification_status",
                    "content",
                    "content_truncated",
                )
            }
            size = len(_json(item))
            if len(documents) >= limit or used + size > MAX_RETRIEVAL_CHARS:
                break
            documents.append(item)
            used += size
        return {
            "status": "evidence_found" if documents else "no_approved_evidence",
            "trust": "untrusted_external_source_material",
            "project_id": project_id,
            "documents": documents,
        }
