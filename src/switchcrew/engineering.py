"""Durable Engineering execution over Switch Console's pinned local control API.

No code generator or local Codex subprocess substitutes for a managed session.
The session must commit its work and write an assignment-bound receipt.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

import httpx


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], stderr=subprocess.PIPE, text=True
    ).strip()


def assignment_id(handoff_id):
    if not handoff_id:
        raise ValueError("A stable handoff ID is required")
    return hashlib.sha256(handoff_id.encode()).hexdigest()[:32]


class Ledger:
    """Separate execution journal. Claim before every non-idempotent external call."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS executions "
            "(id TEXT PRIMARY KEY, spec TEXT NOT NULL, data TEXT NOT NULL)"
        )

    def close(self):
        self.db.close()

    def reserve(self, identity, spec):
        encoded = json.dumps(spec, sort_keys=True)
        self.db.execute(
            "INSERT OR IGNORE INTO executions VALUES(?,?,?)",
            (identity, encoded, json.dumps({"phase": "reserved"})),
        )
        row = self.db.execute("SELECT * FROM executions WHERE id=?", (identity,)).fetchone()
        if row["spec"] != encoded:
            raise ValueError("Assignment inputs changed; use a new handoff")
        return json.loads(row["data"])

    def get(self, identity):
        row = self.db.execute("SELECT data FROM executions WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, identity, data):
        self.db.execute(
            "UPDATE executions SET data=? WHERE id=?", (json.dumps(data, sort_keys=True), identity)
        )

    def claim(self, identity, old, new):
        data = self.get(identity)
        if data["phase"] != old:
            return False
        updated = {**data, "phase": new}
        return (
            self.db.execute(
                "UPDATE executions SET data=? WHERE id=? AND data=?",
                (json.dumps(updated, sort_keys=True), identity, json.dumps(data, sort_keys=True)),
            ).rowcount
            == 1
        )


def outcome(status, identity, **details):
    return {"status": status, "assignment_id": identity, **details}


class ConsoleCodex:
    """Verified pinned Console routes; control token remains outside graph state.

    The supplied Switch client must already be connected to a dedicated test
    room; codex_name must identify the same dedicated agent as console_agent_id.
    Console owns the provider process, credentials and session lifecycle.
    """

    def __init__(
        self,
        control_file,
        console_agent_id,
        codex_name,
        switch,
        console_database=None,
        switch_agent_id=None,
    ):
        self.control_file = Path(control_file)
        self.agent_id = console_agent_id
        self.codex_name = codex_name
        self.switch = switch
        self.console_database = console_database
        self.switch_agent_id = switch_agent_id

    async def request(self, method, suffix, **kwargs):
        if self.control_file.stat().st_mode & 0o077:
            raise ValueError("Control credential file must be private")
        config = json.loads(self.control_file.read_text())
        port = int(config["port"])
        if not 0 < port < 65536:
            raise ValueError("Invalid local control port")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            headers={"x-switch-control-token": config["token"]},
            timeout=20,
            follow_redirects=False,
        ) as client:
            response = await client.request(
                method, f"/agents/{quote(self.agent_id, safe='')}{suffix}", **kwargs
            )
            response.raise_for_status()
            return response.json()

    async def validate(self, checkout):
        agent = (await self.request("GET", ""))["agent"]
        if agent.get("providerId") != "codex" or agent.get("name") != self.codex_name:
            raise ValueError("Dedicated Console Codex agent identity mismatch")
        if not self.switch_agent_id or agent.get("switchAgentId") != self.switch_agent_id:
            raise ValueError("Console and target Switch registration must agree")
        if not self.console_database:
            raise ValueError("Read-only Console database path required to verify local location")
        with sqlite3.connect(
            Path(self.console_database).resolve().as_uri() + "?mode=ro", uri=True
        ) as db:
            row = db.execute(
                "SELECT dir,ssh_host FROM locations WHERE id=?", (agent["locationId"],)
            ).fetchone()
        if not row or row[1] or Path(row[0]).resolve() != checkout:
            raise ValueError("Dedicated Codex location must be the local assignment checkout")

    async def find(self, identity):
        sessions = (await self.request("GET", "/sessions"))["sessions"]
        if any(s["title"] != f"switchcrew:{identity}" for s in sessions):
            raise ValueError("Codex agent must be dedicated to this assignment")
        matches = [s for s in sessions if s["title"] == f"switchcrew:{identity}"]
        if len(matches) > 1:
            raise ValueError("Ambiguous assignment sessions; operator reconciliation required")
        return matches[0]["id"] if matches else None

    async def create(self, identity):
        result = await self.request("POST", "/sessions", json={"title": f"switchcrew:{identity}"})
        return result["session"]["id"]

    async def deliver(self, identity, checkout, question, criteria):
        prompt = json.dumps(
            {
                "assignment_id": identity,
                "checkout": str(checkout),
                "question": question,
                "acceptance_criteria": criteria,
                "instructions": "Work only in this checkout. Implement the request. Commit all "
                "changes. Then atomically write .switchcrew-result.json "
                "(do not commit this receipt) with "
                "assignment_id, status=implemented or implementation_failed, final_revision="
                "the exact full git HEAD. Never publish or push. Do not modify QA commands.",
            },
            sort_keys=True,
        )
        result = await self.switch.send_targeted_message(prompt, target_names=[self.codex_name])
        if result.get("target_statuses", {}).get(self.codex_name) != "live":
            raise ValueError("Managed Codex session is not live; delivery uncertain")
        return result.get("event_id")


class EngineeringExecutor:
    def __init__(
        self,
        ledger,
        root,
        source,
        base_revision,
        session,
        authorize=None,
        review_base_revision=None,
        expected_source_revision=None,
    ):
        self.ledger, self.root = ledger, Path(root).resolve()
        self.source, self.base = str(Path(source).resolve()), base_revision
        self.session = session
        self.authorize = authorize
        self.review_base = review_base_revision
        self.expected_source_revision = expected_source_revision

    def check_approval(self, state):
        if self.authorize is not None:
            self.authorize(state)

    async def __call__(self, state):
        identity = assignment_id(state["handoff_id"])
        try:
            self.check_approval(state)
            return await self.execute(state)
        except PermissionError:
            return outcome("blocked", identity, reason="human_engineering_approval_required")
        except (OSError, ValueError, subprocess.CalledProcessError):
            return outcome(
                "blocked_configuration", identity, reason="assignment_checkout_or_inputs_invalid"
            )

    async def execute(self, state):
        identity = assignment_id(state["handoff_id"])
        source = self.source
        requested_base = self.base
        if self.expected_source_revision:
            if not Path(source).is_dir():
                return outcome(
                    "blocked_configuration", identity, reason="correction_source_invalid"
                )
            if git(source, "rev-parse", "HEAD") != self.expected_source_revision:
                return outcome(
                    "blocked_configuration", identity, reason="correction_source_revision_changed"
                )
        checkout_base = git(source, "rev-parse", f"{requested_base}^{{commit}}")
        review_base = git(source, "rev-parse", f"{(self.review_base or checkout_base)}^{{commit}}")
        git(source, "merge-base", "--is-ancestor", review_base, checkout_base)
        spec = {
            "source": source,
            "base_revision": review_base,
            "question": state["question"],
            "acceptance_criteria": state["acceptance_criteria"],
        }
        # Preserve immutable pre-correction ledger inputs for initial attempts.
        # Only correction assignments need a distinct checkout starting revision.
        if self.expected_source_revision is not None or review_base != checkout_base:
            spec["checkout_base_revision"] = checkout_base
        data = self.ledger.reserve(identity, spec)
        checkout = self.root / identity
        if "result" in data:
            return data["result"]
        if checkout.is_symlink() or checkout.resolve() == Path(self.source):
            return outcome("blocked_configuration", identity, reason="checkout_not_isolated")
        if not checkout.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--no-hardlinks", "--no-checkout", source, str(checkout)],
                check=True,
                capture_output=True,
            )
            git(checkout, "checkout", "--detach", checkout_base)
            with (checkout / ".git/info/exclude").open("a") as excludes:
                excludes.write("\n.claude/settings.local.json\n.switchcrew-result.json\n")
        # Console onboarding writes private local connection configuration. Keep it
        # out of the implementation tree, including checkouts prepared before onboarding.
        exclude = checkout / ".git/info/exclude"
        if "/.switch/config/" not in exclude.read_text().splitlines():
            with exclude.open("a") as excludes:
                excludes.write("\n/.switch/config/\n")
        # A partly created checkout is never silently reset on retry.
        if data["phase"] == "reserved" and (
            git(checkout, "rev-parse", "HEAD") != checkout_base
            or git(checkout, "status", "--porcelain")
        ):
            return outcome("blocked_configuration", identity, reason="checkout_not_clean_at_base")
        if self.session is None:
            return outcome(
                "blocked_configuration",
                identity,
                checkout=str(checkout),
                base_revision=review_base,
                checkout_base_revision=checkout_base,
                reason="managed_codex_session_not_configured",
            )
        try:
            await self.session.validate(checkout)
        except (OSError, ValueError, KeyError, sqlite3.Error, httpx.HTTPError):
            # Once an external action has been claimed, its outcome may still
            # arrive. Preserve the handoff until identity can be verified again.
            if self.ledger.get(identity)["phase"] != "reserved":
                return outcome(
                    "pending_execution",
                    identity,
                    checkout=str(checkout),
                    base_revision=review_base,
                    checkout_base_revision=checkout_base,
                    reason="managed_codex_identity_unavailable_or_misconfigured",
                )
            return outcome(
                "blocked_configuration",
                identity,
                checkout=str(checkout),
                base_revision=review_base,
                checkout_base_revision=checkout_base,
                reason="managed_codex_agent_unavailable_or_misconfigured",
            )
        try:
            sid = data.get("session_id") or await self.session.find(identity)
            if sid is None:
                if not self.ledger.claim(identity, "reserved", "creating_session"):
                    return outcome(
                        "pending_execution",
                        identity,
                        reason="uncertain_session_creation_reconcile_by_title",
                    )
                self.check_approval(state)
                sid = await self.session.create(identity)
            data = self.ledger.get(identity)
            if data["phase"] in {"reserved", "creating_session"}:
                data.update(
                    phase="session_ready",
                    session_id=sid,
                    checkout=str(checkout),
                    base_revision=review_base,
                    checkout_base_revision=checkout_base,
                )
                self.ledger.put(identity, data)
            if self.ledger.claim(identity, "session_ready", "delivering"):
                self.check_approval(state)
                event = await self.session.deliver(
                    identity, checkout, state["question"], state["acceptance_criteria"]
                )
                data = self.ledger.get(identity)
                data.update(phase="pending", delivery_event_id=event)
                self.ledger.put(identity, data)
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            return outcome(
                "pending_execution",
                identity,
                reason="external_response_uncertain",
                checkout=str(checkout),
                base_revision=review_base,
                checkout_base_revision=checkout_base,
            )
        receipt = checkout / ".switchcrew-result.json"
        if not receipt.exists():
            return outcome(
                "pending_execution",
                identity,
                reason="awaiting_managed_session_receipt",
                checkout=str(checkout),
                base_revision=review_base,
                checkout_base_revision=checkout_base,
            )
        try:
            record = json.loads(receipt.read_text())
            if not isinstance(record, dict):
                raise ValueError("Receipt must be an object")
            final = git(checkout, "rev-parse", "HEAD")
            if record["assignment_id"] != identity or record["final_revision"] != final:
                raise ValueError("Receipt identity/revision differs")
            git(checkout, "merge-base", "--is-ancestor", checkout_base, final)
            git(checkout, "merge-base", "--is-ancestor", review_base, final)
            dirty = git(checkout, "status", "--porcelain", "--untracked-files=all").splitlines()
            protected = git(
                checkout,
                "ls-files",
                ".claude/settings.local.json",
                ".switchcrew-result.json",
                ".switch/config/",
            )
            if protected or any(line != "?? .switchcrew-result.json" for line in dirty):
                raise ValueError("Implementation has uncommitted changes")
            status = record["status"]
            if status not in {"implemented", "implementation_failed"}:
                raise ValueError("Invalid receipt status")
            result = outcome(
                status,
                identity,
                checkout=str(checkout),
                base_revision=review_base,
                checkout_base_revision=checkout_base,
                final_revision=final,
                session_id=sid,
                diff=git(checkout, "diff", "--binary", review_base, final),
            )
        except (KeyError, ValueError, OSError, subprocess.CalledProcessError):
            return outcome("implementation_failed", identity, reason="invalid_revision_receipt")
        data = self.ledger.get(identity)
        data.update(phase="finished", result=result)
        self.ledger.put(identity, data)
        return result


def qa_handoff_id(engineering_handoff, revision):
    return "qa-" + hashlib.sha256(f"{engineering_handoff}:{revision}".encode()).hexdigest()


def enqueue_independent_qa(store, engineering_handoff, engineering_agent, qa_agent):
    """Restart-safe cross-agent handoff. Only committed implementation results qualify."""
    if engineering_agent == qa_agent:
        raise ValueError("Engineering and QA identities must differ")
    handoff = store.get_handoff(engineering_handoff)
    if not handoff or handoff["recipient"] != engineering_agent:
        raise ValueError("Engineering handoff identity mismatch")
    result = handoff.get("result") or {}
    if handoff["status"] != "completed" or result.get("status") != "implemented":
        return None
    revision = result["final_revision"]
    return store.create_handoff(
        handoff["task_id"],
        engineering_agent,
        qa_agent,
        handoff["question"],
        handoff["acceptance_criteria"],
        None,
        handoff_id=qa_handoff_id(engineering_handoff, revision),
        require_active=True,
    )
