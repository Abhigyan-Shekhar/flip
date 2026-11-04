"""Verified correction lessons with local ACLs and conservative Waggle projection.

SQLite owns lesson status, evidence, applicability, and relations. Waggle only
receives approved project-wide summaries through its existing observation tool;
it does not provide our ACLs or a supported relation-write API.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from .approval import ApprovalWorkflow
from .engineering import assignment_id, qa_handoff_id
from .store import encode

_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_LESSON_ID = re.compile(r"lesson-[0-9a-f]{24}")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token|credential|authorization)"
    r"[\"']?\s*[:=]\s*[\"']?\S+|(?:sk-|sk_live_|sk_test_|ghp_|github_pat_)"
    r"[A-Za-z0-9_.-]{12,}|-----BEGIN .*PRIVATE KEY-----)"
)
_RELATIONS = {"supersedes", "contradicts"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe(value: Any, *, limit: int = 2000, name: str = "text") -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be bounded non-empty text")
    if _SECRET.search(value):
        raise ValueError(f"{name} contains sensitive data")
    return value.strip()


class LessonService:
    """Build and approve lessons only from a durable QA/Reviewer correction chain.

    `trusted_actors` is operator-authenticated policy, not task or lesson input.
    Project/team/org memberships and approver lists are supplied by the host.
    """

    def __init__(self, store, *, trusted_actors: dict[str, dict[str, Any]], memory=None) -> None:
        if not trusted_actors:
            raise ValueError("At least one operator configured actor is required")
        self.store = store
        self.trusted_actors = {
            actor: {
                key: tuple(values) for key, values in profile.items() if isinstance(values, list)
            }
            for actor, profile in trusted_actors.items()
            if isinstance(actor, str) and actor.strip() and isinstance(profile, dict)
        }
        self.memory = memory
        self.approvals = ApprovalWorkflow(store)
        with store._lock:
            store.connection.execute(
                """CREATE TABLE IF NOT EXISTS verified_lessons (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    failure_signature TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    root_cause_status TEXT NOT NULL,
                    correction TEXT NOT NULL,
                    regression_command TEXT NOT NULL,
                    applicability TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    evidence_fingerprint TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    proposed_relations TEXT NOT NULL DEFAULT '[]',
                    approved_by TEXT,
                    approved_at TEXT,
                    waggle_write_status TEXT NOT NULL,
                    waggle_warning TEXT,
                    superseded_by TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, evidence_fingerprint)
                )"""
            )
            store.connection.execute(
                """CREATE TABLE IF NOT EXISTS lesson_relations (
                    source_id TEXT NOT NULL REFERENCES verified_lessons(id),
                    target_id TEXT NOT NULL REFERENCES verified_lessons(id),
                    relation TEXT NOT NULL CHECK(relation IN ('supersedes','contradicts')),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, target_id, relation),
                    CHECK(source_id <> target_id)
                )"""
            )
            store.connection.execute(
                "CREATE INDEX IF NOT EXISTS verified_lessons_scope "
                "ON verified_lessons(project_id, approval_status, scope)"
            )
            store.connection.execute(
                """CREATE TABLE IF NOT EXISTS lesson_retrievals (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    lesson_id TEXT NOT NULL REFERENCES verified_lessons(id),
                    actor_id TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, lesson_id, actor_id)
                )"""
            )
            store.connection.execute(
                """CREATE TABLE IF NOT EXISTS lesson_uses (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    lesson_id TEXT NOT NULL REFERENCES verified_lessons(id),
                    actor_id TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome='applied_and_verified'),
                    evidence TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, lesson_id)
                )"""
            )

    def _actor(self, actor_id: str) -> dict[str, Any]:
        if not isinstance(actor_id, str) or actor_id not in self.trusted_actors:
            raise PermissionError("Actor is not operator-authorized")
        return self.trusted_actors[actor_id]

    @staticmethod
    def _has(profile: dict[str, Any], key: str, value: str) -> bool:
        return value in profile.get(key, ())

    def _scope(self, actor_id: str, project_id: str, scope: str, scope_id: str | None) -> str:
        profile = self._actor(actor_id)
        if project_id not in profile.get("projects", ()):
            raise PermissionError("Actor is not a member of this project")
        if scope == "personal":
            if scope_id not in (None, actor_id):
                raise PermissionError("Personal scope is bound to the authenticated owner")
            return actor_id
        if scope == "project":
            if scope_id not in (None, project_id):
                raise PermissionError("Project scope must match project_id")
            return project_id
        if scope == "team" and scope_id and self._has(profile, "teams", scope_id):
            return scope_id
        if scope == "org" and scope_id and self._has(profile, "orgs", scope_id):
            return scope_id
        if scope not in {"team", "org"}:
            raise ValueError("scope must be personal, team, org, or project")
        raise PermissionError("Actor is not a member of this scope")

    def _can_approve(self, actor_id: str, row: dict[str, Any]) -> bool:
        profile = self._actor(actor_id)
        scope = row["scope"]
        scope_id = row["scope_id"]
        if scope == "personal":
            return actor_id == row["owner_id"] or self._has(
                profile, "approve_personal", row["owner_id"]
            )
        policy = {
            "team": ("approve_teams", scope_id),
            "org": ("approve_orgs", scope_id),
            "project": ("approve_projects", row["project_id"]),
        }.get(scope)
        return bool(policy and self._has(profile, policy[0], policy[1]))

    def _can_read(self, actor_id: str, row: dict[str, Any]) -> bool:
        profile = self._actor(actor_id)
        scope = row["scope"]
        if scope == "personal":
            return actor_id == row["owner_id"]
        if scope == "project":
            return self._has(profile, "projects", row["project_id"])
        if scope == "team":
            return self._has(profile, "teams", row["scope_id"])
        if scope == "org":
            return self._has(profile, "orgs", row["scope_id"])
        return False

    def _evidence(self, task_id: str, regression_command: list[str]) -> tuple[dict[str, Any], str]:
        """Revalidate workflow receipts against their exact revisions and actors."""
        task = self.store.get_task(task_id)
        source = task.get("source") if task else None
        if not isinstance(source, dict) or not isinstance(source.get("project_id"), str):
            raise PermissionError(
                "Task must have a project identity before it can produce a lesson"
            )
        data = self.approvals.get(task_id)
        if (
            not task
            or not data
            or task["status"]
            not in {"HUMAN_REVIEW", "PUBLISH_APPROVED", "PUBLISHING", "PR_OPEN", "COMPLETED"}
            or data.get("phase") not in {"human_review", "release", "completed"}
        ):
            raise PermissionError(
                "A completed correction with QA and Reviewer evidence is required"
            )
        history = data.get("retry_history")
        if not isinstance(history, list) or not history or data.get("attempt") != len(history):
            raise PermissionError("A verified correction attempt is required")
        revision = data.get("revision")
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            raise PermissionError("Final revision is not a full Git SHA")
        qa_handoff = self.store.get_handoff(data.get("qa_handoff"))
        reviewer_handoff = self.store.get_handoff(data.get("review_handoff"))
        try:
            qa_result = self.approvals._validate_qa(data, qa_handoff)
            reviewer_result = self.approvals._validate_review(data, reviewer_handoff)
        except (KeyError, TypeError, PermissionError, AttributeError) as exc:
            raise PermissionError("Exact final QA and Reviewer receipts are required") from exc
        if (
            qa_result.get("tested_revision") != revision
            or reviewer_result.get("reviewed_revision") != revision
        ):
            raise PermissionError("QA and Reviewer must validate the same final revision")
        if not any(
            isinstance(command, dict)
            and command.get("command") == regression_command
            and command.get("exit_code") == 0
            and command.get("timed_out") is False
            and command.get("revision") == revision
            for command in qa_result.get("commands", [])
        ):
            raise PermissionError("The named regression command did not pass on the final revision")

        specs = data.get("spec", {})
        engineering, qa, reviewer = specs.get("engineering"), specs.get("qa"), specs.get("reviewer")
        rejected = []
        for expected_attempt, entry in enumerate(history, start=1):
            if not isinstance(entry, dict) or entry.get("attempt") != expected_attempt:
                raise PermissionError("Correction history is incomplete")
            if not all(isinstance(item, dict) for item in entry.get("findings", [])):
                raise PermissionError("Correction findings are malformed")
            failed_handoff = self.store.get_handoff(entry.get("rejected_handoff"))
            if (
                not failed_handoff
                or failed_handoff.get("task_id") != task_id
                or failed_handoff.get("status") != "completed"
                or not _SHA.fullmatch(str(entry.get("revision", "")))
                or not isinstance(entry.get("findings"), list)
                or not entry["findings"]
                or not isinstance(entry.get("implementation_assignment"), str)
            ):
                raise PermissionError("Correction is missing revision-bound failure evidence")
            failed_result = failed_handoff.get("result")
            common = (
                entry["implementation_assignment"] == failed_result.get("implementation_assignment")
                if isinstance(failed_result, dict)
                else False
            )
            failed_revision = entry["revision"]
            with self.store._lock:
                old_engineering_ids = [
                    row[0]
                    for row in self.store.connection.execute(
                        "SELECT id FROM handoffs WHERE task_id=?", (task_id,)
                    ).fetchall()
                ]
            prior_engineering_id = next(
                (
                    handoff_id
                    for handoff_id in old_engineering_ids
                    if qa_handoff_id(handoff_id, failed_revision) == failed_handoff["id"]
                    or "review-"
                    + hashlib.sha256(f"{handoff_id}:{failed_revision}".encode()).hexdigest()
                    == failed_handoff["id"]
                ),
                None,
            )
            prior_engineering = (
                self.store.get_handoff(prior_engineering_id) if prior_engineering_id else None
            )
            prior_result = prior_engineering.get("result") if prior_engineering else None
            attempt_data = dict(data)
            attempt_data.update(engineering_handoff=prior_engineering_id, revision=failed_revision)
            if not (
                prior_engineering
                and prior_engineering.get("status") == "completed"
                and isinstance(prior_result, dict)
                and prior_result.get("status") == "implemented"
                and prior_result.get("final_revision") == failed_revision
                and assignment_id(prior_engineering_id) == entry["implementation_assignment"]
            ):
                raise PermissionError(
                    "Correction does not link to its exact implementation revision"
                )
            if entry.get("rejecting_agent") == qa:
                try:
                    expected_findings = self.approvals._qa_failure_findings(
                        task_id, attempt_data, failed_handoff
                    )
                    valid_failure = (
                        failed_handoff.get("sender") == engineering
                        and failed_handoff.get("recipient") == qa
                        and common
                        and entry["findings"] == expected_findings
                    )
                except (PermissionError, KeyError, TypeError, AttributeError):
                    valid_failure = False
            elif entry.get("rejecting_agent") == reviewer:
                try:
                    validated_failure = self.approvals._validate_review(
                        attempt_data, failed_handoff, allow_rejected=True
                    )
                    expected_findings = [
                        *(validated_failure.get("findings") or []),
                        *(validated_failure.get("command_findings") or []),
                    ]
                    valid_failure = (
                        failed_handoff.get("sender") == qa
                        and failed_handoff.get("recipient") == reviewer
                        and common
                        and entry["findings"] == expected_findings
                    )
                except (PermissionError, KeyError, TypeError, AttributeError):
                    valid_failure = False
            else:
                valid_failure = False
            if not valid_failure:
                raise PermissionError("Correction rejection lacks exact QA or Reviewer evidence")
            rejected.append(
                {
                    "handoff_id": failed_handoff["id"],
                    "revision": entry["revision"],
                    "rejecting_agent": entry["rejecting_agent"],
                    "attempt": expected_attempt,
                    "findings_sha256": hashlib.sha256(
                        encode(entry["findings"]).encode()
                    ).hexdigest(),
                }
            )
        safe_qa_commands = [
            {
                "exit_code": command.get("exit_code"),
                "timed_out": command.get("timed_out"),
                "revision": command.get("revision"),
                "output_sha256": command.get("output_sha256"),
            }
            for command in qa_result.get("commands", [])
        ]
        evidence = {
            "task_id": task_id,
            "project_id": source["project_id"],
            "final_revision": revision,
            "qa_handoff_id": qa_handoff["id"],
            "reviewer_handoff_id": reviewer_handoff["id"],
            "qa_status": qa_result["status"],
            "reviewer_status": reviewer_result["status"],
            "qa_receipt_sha256": hashlib.sha256(encode(safe_qa_commands).encode()).hexdigest(),
            "review_receipt_sha256": hashlib.sha256(encode(reviewer_result).encode()).hexdigest(),
            "correction_attempts": rejected,
            "regression_command_sha256": hashlib.sha256(
                encode(regression_command).encode()
            ).hexdigest(),
        }
        fingerprint = hashlib.sha256(encode(evidence).encode()).hexdigest()
        return evidence, fingerprint

    def propose_from_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        project_id: str,
        scope: str,
        summary: str,
        failure_signature: str,
        root_cause: str,
        root_cause_status: str,
        correction: str,
        regression_command: list[str],
        applicability: list[str],
        scope_id: str | None = None,
        supersedes: list[str] | None = None,
        contradicts: list[str] | None = None,
    ) -> dict[str, Any]:
        scope_id = self._scope(actor_id, project_id, scope, scope_id)
        summary = _safe(summary, name="summary")
        failure_signature = _safe(failure_signature, name="failure_signature")
        root_cause = _safe(root_cause, name="root_cause")
        correction = _safe(correction, name="correction")
        if root_cause_status not in {"verified", "unknown", "hypothesis"}:
            raise ValueError("root_cause_status must be verified, unknown, or hypothesis")
        if root_cause_status == "unknown" and root_cause.casefold() != "unknown":
            raise ValueError("Unknown root cause must be recorded literally as 'unknown'")
        if root_cause_status == "hypothesis":
            # Hypotheses can be retained for human review but never promoted to verified memory.
            approval_status = "hypothesis"
        else:
            approval_status = "candidate"
        if (
            not isinstance(regression_command, list)
            or not regression_command
            or any(
                not isinstance(part, str) or not part or len(part) > 256
                for part in regression_command
            )
        ):
            raise ValueError("regression_command must be a bounded argv list")
        if _SECRET.search(encode(regression_command)):
            raise ValueError("regression_command contains sensitive data")
        if not isinstance(applicability, list) or not 1 <= len(applicability) <= 20:
            raise ValueError("applicability must contain 1 through 20 bounded items")
        applicability = [_safe(item, limit=300, name="applicability") for item in applicability]
        supersedes = supersedes or []
        contradicts = contradicts or []
        if any(not isinstance(item, str) or not item for item in [*supersedes, *contradicts]):
            raise ValueError("Lesson relation targets must be IDs")
        evidence, fingerprint = self._evidence(task_id, regression_command)
        if evidence["project_id"] != project_id:
            raise PermissionError("Lesson project must match the task's persisted project")
        lesson_id = (
            "lesson-" + hashlib.sha256(f"{task_id}\0{fingerprint}".encode()).hexdigest()[:24]
        )
        with self.store._lock, self.store.transaction():
            self.store.connection.execute(
                """INSERT OR IGNORE INTO verified_lessons
                (id, task_id, project_id, owner_id, scope, scope_id, summary,
                 failure_signature, root_cause, root_cause_status, correction,
                 regression_command, applicability, evidence, evidence_fingerprint,
                 approval_status, proposed_relations, approved_by, approved_at, waggle_write_status,
                 waggle_warning, superseded_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                        'not_started', NULL, NULL, ?)""",
                (
                    lesson_id,
                    task_id,
                    project_id,
                    actor_id,
                    scope,
                    scope_id,
                    summary,
                    failure_signature,
                    root_cause,
                    root_cause_status,
                    correction,
                    encode(regression_command),
                    encode(applicability),
                    encode(evidence),
                    fingerprint,
                    approval_status,
                    encode({"supersedes": supersedes, "contradicts": contradicts}),
                    _now(),
                ),
            )
            row = self.store.connection.execute(
                "SELECT * FROM verified_lessons WHERE id=?", (lesson_id,)
            ).fetchone()
        return self._public(dict(row))

    def _validate_relations(
        self, lesson: dict[str, Any], actor_id: str, relation_ids: list[str], relation: str
    ):
        if relation not in _RELATIONS:
            raise ValueError("Unsupported lesson relation")
        validated = []
        for target_id in relation_ids:
            target = self._get_row(target_id)
            if (
                target is None
                or target["approval_status"] != "approved"
                or target["project_id"] != lesson["project_id"]
                or target["scope"] != lesson["scope"]
                or target["scope_id"] != lesson["scope_id"]
                or not self._can_read(actor_id, target)
            ):
                raise PermissionError("Lesson relation target must be approved in the same scope")
            validated.append(target_id)
        return validated

    async def approve_lesson(self, lesson_id: str, *, actor_id: str) -> dict[str, Any]:
        """Approve once; uncertain Waggle writes are never blindly retried."""
        should_write = False
        row = None
        with self.store._lock, self.store.transaction():
            raw = self._get_row(lesson_id)
            if raw is None:
                raise KeyError("Lesson not found")
            if not self._can_approve(actor_id, raw):
                raise PermissionError("Actor cannot approve this lesson scope")
            if raw["root_cause_status"] == "hypothesis" or raw["approval_status"] == "hypothesis":
                raise PermissionError("An unverified hypothesis cannot be approved as a lesson")
            if raw["approval_status"] == "rejected":
                raise ValueError("Rejected lessons cannot be approved")
            try:
                current_evidence, current_fingerprint = self._evidence(
                    raw["task_id"], json.loads(raw["regression_command"])
                )
            except PermissionError as exc:
                raise PermissionError(
                    "Lesson evidence is stale or no longer verifies against current "
                    "QA/Reviewer data"
                ) from exc
            if current_fingerprint != raw["evidence_fingerprint"]:
                raise PermissionError(
                    "Lesson evidence is stale; propose again from current QA/Reviewer data"
                )
            if current_evidence["project_id"] != raw["project_id"]:
                raise PermissionError(
                    "Lesson project no longer matches the task's persisted project"
                )
            if raw["approval_status"] != "approved":
                relations = json.loads(raw["proposed_relations"])
                supersedes = self._validate_relations(
                    raw, actor_id, relations.get("supersedes", []), "supersedes"
                )
                contradicts = self._validate_relations(
                    raw, actor_id, relations.get("contradicts", []), "contradicts"
                )
                if set(supersedes) & set(contradicts):
                    raise ValueError("A lesson cannot both supersede and contradict one target")
                self.store.connection.execute(
                    "UPDATE verified_lessons SET approval_status='approved', approved_by=?, "
                    "approved_at=? WHERE id=?",
                    (actor_id, _now(), lesson_id),
                )
                for target_id in supersedes:
                    self.store.connection.execute(
                        "INSERT OR IGNORE INTO lesson_relations VALUES (?, ?, 'supersedes', ?, ?)",
                        (lesson_id, target_id, actor_id, _now()),
                    )
                    self.store.connection.execute(
                        "UPDATE verified_lessons SET superseded_by=? WHERE id=?",
                        (lesson_id, target_id),
                    )
                for target_id in contradicts:
                    for source_id, destination_id in (
                        (lesson_id, target_id),
                        (target_id, lesson_id),
                    ):
                        self.store.connection.execute(
                            "INSERT OR IGNORE INTO lesson_relations "
                            "VALUES (?, ?, 'contradicts', ?, ?)",
                            (source_id, destination_id, actor_id, _now()),
                        )
            raw = self._get_row(lesson_id)
            if raw["waggle_write_status"] == "not_started":
                memory_project = getattr(self.memory, "project", None)
                if (
                    raw["scope"] == "project"
                    and self.memory is not None
                    and memory_project == raw["project_id"]
                ):
                    self.store.connection.execute(
                        "UPDATE verified_lessons SET waggle_write_status='in_flight' WHERE id=? "
                        "AND waggle_write_status='not_started'",
                        (lesson_id,),
                    )
                    should_write = (
                        self.store.connection.execute("SELECT changes()").fetchone()[0] == 1
                    )
                else:
                    self.store.connection.execute(
                        "UPDATE verified_lessons SET waggle_write_status='local_only' WHERE id=?",
                        (lesson_id,),
                    )
            elif raw["waggle_write_status"] == "in_flight" and not raw["waggle_warning"]:
                self.store.connection.execute(
                    "UPDATE verified_lessons SET waggle_warning=? WHERE id=?",
                    ("Waggle write outcome is uncertain; no automatic retry was made.", lesson_id),
                )
            row = self._get_row(lesson_id)
        if should_write:
            # The write occurs once, outside the SQLite transaction. If it times out,
            # its outcome is ambiguous; leave it unknown and require operator review.
            try:
                evidence = json.loads(row["evidence"])
                structured = {
                    "record_type": "switchcrew_verified_failure_lesson_v1",
                    "lesson_id": lesson_id,
                    "project_id": row["project_id"],
                    "scope": row["scope"],
                    "summary": row["summary"],
                    "failure_signature": row["failure_signature"],
                    "root_cause": row["root_cause"],
                    "root_cause_status": row["root_cause_status"],
                    "correction": row["correction"],
                    "applicability": json.loads(row["applicability"]),
                    "evidence": evidence,
                    "relations": self._relation_summary(lesson_id),
                    "approved_by": row["approved_by"],
                    "approved_at": row["approved_at"],
                }
                user_text = (
                    f"Approved verified correction lesson {lesson_id}; "
                    "explicit human approval recorded."
                )
                response_text = encode(structured)
                if _SECRET.search(user_text + response_text):
                    state, warning = (
                        "unknown",
                        "Waggle write omitted: sensitive lesson data detected.",
                    )
                else:
                    result = await self.memory.observe(
                        user_text,
                        response_text,
                        "engineering",
                        row["task_id"],
                    )
                    state = "stored" if result.persisted is True else "unknown"
                    warning = (
                        None
                        if result.persisted is True
                        else "Waggle write was not confirmed; no automatic retry was made."
                    )
            except Exception:
                state, warning = (
                    "unknown",
                    "Waggle write outcome is uncertain; no automatic retry was made.",
                )
            with self.store._lock, self.store.transaction():
                self.store.connection.execute(
                    "UPDATE verified_lessons SET waggle_write_status=?, waggle_warning=? "
                    "WHERE id=? "
                    "AND waggle_write_status='in_flight'",
                    (state, warning, lesson_id),
                )
                row = self._get_row(lesson_id)
        return self._public(row)

    async def retrieve_lessons(
        self,
        query: str,
        *,
        actor_id: str,
        project_id: str,
        task_id: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        profile = self._actor(actor_id)
        if not self._has(profile, "projects", project_id):
            raise PermissionError("Actor is not a member of this project")
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("query must be bounded text")
        if _SECRET.search(query):
            return {"status": "omitted_sensitive_query", "lessons": [], "warnings": []}
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be from 1 through 20")
        with self.store._lock:
            rows = self.store.connection.execute(
                "SELECT * FROM verified_lessons WHERE project_id=? AND approval_status='approved'",
                (project_id,),
            ).fetchall()
        terms = {part.casefold() for part in re.findall(r"[\w.-]{2,}", query)}
        ranked = []
        for raw in rows:
            row = dict(raw)
            if not self._can_read(actor_id, row):
                continue
            if row["superseded_by"]:
                continue
            searchable = " ".join(
                [
                    row["summary"],
                    row["failure_signature"],
                    row["root_cause"],
                    row["correction"],
                    row["applicability"],
                ]
            )
            overlap = len(
                terms & {part.casefold() for part in re.findall(r"[\w.-]{2,}", searchable)}
            )
            if terms and overlap == 0:
                continue
            ranked.append((overlap, row))

        warnings: list[str] = []
        waggle_project = getattr(self.memory, "project", None)
        cited_ids: set[str] = set()
        if self.memory is not None and waggle_project == project_id:
            try:
                recall = await self.memory.recall(query, "engineering", task_id)
                if recall.warning:
                    warnings.append(
                        "Waggle recall unavailable; local approved lessons remain available."
                    )
                else:
                    # Waggle text is untrusted: it can select only an ID that is also
                    # present in this local, approved, ACL-checked database.
                    cited_ids = set(_LESSON_ID.findall(recall.context))
                    known_ids = {row["id"] for _, row in ranked}
                    cited_ids.intersection_update(known_ids)
            except Exception:
                warnings.append(
                    "Waggle recall unavailable; local approved lessons remain available."
                )
        lessons = [
            self._public(row, include_evidence=False)
            for _, row in sorted(
                ranked,
                key=lambda item: (
                    item[1]["id"] not in cited_ids,
                    -item[0],
                    item[1]["created_at"],
                ),
            )[:limit]
        ]
        for lesson in lessons:
            lesson["relations"] = self._relation_summary(lesson["id"])
        if task_id and lessons:
            if self.store.get_task(task_id) is None:
                raise ValueError("task_id must identify a persisted consuming task")
            with self.store._lock, self.store.transaction():
                for lesson in lessons:
                    self.store.connection.execute(
                        "INSERT OR IGNORE INTO lesson_retrievals VALUES (?, ?, ?, ?)",
                        (task_id, lesson["id"], actor_id, _now()),
                    )
        return {
            "status": "lessons_found" if lessons else "no_verified_lessons",
            "trust": "untrusted_guidance_verify_against_current_repository_and_task",
            "project_id": project_id,
            "lessons": lessons,
            "warnings": warnings,
        }

    def record_use(self, task_id: str, lesson_id: str, *, actor_id: str) -> dict[str, Any]:
        """Record lesson use only after the consuming task passes its own evidence chain.

        Retrieval is recorded when `retrieve_lessons(..., task_id=...)` returns the
        lesson. Use is confirmed only when the task later has exact passing QA and
        Reviewer receipts and passes the lesson's named regression command.
        """
        row = self._get_row(lesson_id)
        if row is None or row["approval_status"] != "approved":
            raise PermissionError("Only an approved lesson can have a use receipt")
        if not self._can_read(actor_id, row):
            raise PermissionError("Actor cannot access this lesson")
        with self.store._lock:
            retrieved = self.store.connection.execute(
                "SELECT 1 FROM lesson_retrievals WHERE task_id=? AND lesson_id=? AND actor_id=?",
                (task_id, lesson_id, actor_id),
            ).fetchone()
        if retrieved is None:
            raise PermissionError("Lesson must be retrieved for this task before recording use")
        command = json.loads(row["regression_command"])
        evidence, fingerprint = self._evidence(task_id, command)
        if evidence["project_id"] != row["project_id"]:
            raise PermissionError("Use task project must match the lesson project")
        if task_id == row["task_id"]:
            raise PermissionError("A lesson cannot be recorded as reused by its source task")
        with self.store._lock, self.store.transaction():
            self.store.connection.execute(
                "INSERT OR IGNORE INTO lesson_uses VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    lesson_id,
                    actor_id,
                    "applied_and_verified",
                    encode({"evidence_fingerprint": fingerprint, **evidence}),
                    _now(),
                ),
            )
            use = self.store.connection.execute(
                "SELECT * FROM lesson_uses WHERE task_id=? AND lesson_id=?",
                (task_id, lesson_id),
            ).fetchone()
        value = dict(use)
        value["evidence"] = json.loads(value["evidence"])
        return value

    def _get_row(self, lesson_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT * FROM verified_lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        return dict(row) if row else None

    def _relation_summary(self, lesson_id: str) -> list[dict[str, str]]:
        with self.store._lock:
            rows = self.store.connection.execute(
                "SELECT source_id,target_id,relation FROM lesson_relations "
                "WHERE source_id=? OR target_id=? ORDER BY relation,source_id,target_id",
                (lesson_id, lesson_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _public(row: dict[str, Any], *, include_evidence: bool = True) -> dict[str, Any]:
        value = {
            key: row[key]
            for key in (
                "id",
                "task_id",
                "project_id",
                "owner_id",
                "scope",
                "scope_id",
                "summary",
                "failure_signature",
                "root_cause",
                "root_cause_status",
                "correction",
                "approval_status",
                "approved_by",
                "approved_at",
                "waggle_write_status",
                "waggle_warning",
                "superseded_by",
                "created_at",
            )
        }
        value["applicability"] = json.loads(row["applicability"])
        value["regression_command"] = json.loads(row["regression_command"])
        if include_evidence:
            value["evidence"] = json.loads(row["evidence"])
        return value
