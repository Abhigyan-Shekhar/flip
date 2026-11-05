"""Bounded scanner investigation over the existing Switch handoffs and approvals.

All executable policy lives in private worker configuration. Chat selects only
this story; it never supplies a command, checkout, destination or approval.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .crew import _display, _task_scope
from .engineering import git
from .execution import capture_command

# An operator-owned reproduction against the actual imported source revision.
# The failing assertion is evidence, not a simulated scanner or canned log.
REPRODUCTION = """import asyncio,json,sys
sys.path.insert(0,'src')
from switchcrew.security import ScannerConfig,SecurityRunner
async def main():
    import subprocess
    revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    report=await SecurityRunner([ScannerConfig('incomplete-native-contract',
        (sys.executable,'-c','import json;print(json.dumps(dict(Results=[])))'))]).run('.',revision)
    print(json.dumps(report.to_dict(),default=list),flush=True)
    message='Incomplete native Trivy output was accepted as '+report.status
    assert report.status=='scanner_failed', message
asyncio.run(main())
"""


def investigation_intent(text):
    text = text.strip().lower()
    return (
        len(text) <= 1000
        and not text.startswith(("{", "["))
        and bool(re.search(r"\b(trivy|vulnerability scanner)\b", text))
        and bool(re.search(r"\b(incomplete|false.clean|reports? clean|clean results)\b", text))
        and bool(re.search(r"\binvestigate\b", text))
    )


_READ = {
    "what did you find": "evidence",
    "show me the evidence": "evidence",
    "which functions are affected": "evidence",
    "can you reproduce the bug": "evidence",
    "show me the reproduction logs": "evidence",
    "show me the tests": "logs",
    "show the review": "logs",
    "show me the pr": "status",
    "what's the status": "status",
    "what is the status": "status",
}
_FIX = re.compile(
    r"(?:please\s+)?(?:fix it|go ahead and implement the fix|"
    r"add a regression test and open a pr)"
    r"(?:[.!]?\s*(?:add a regression test and (?:create|open) a pr|"
    r"don['’]t merge without my approval))*[.!]*",
    re.I,
)


def _scope(store, source):
    rows = store.connection.execute(
        "SELECT * FROM tasks WHERE json_extract(source,'$.kind')='green_check' "
        "AND json_extract(source,'$.room_id')=? "
        "AND json_extract(source,'$.user_id')=? LIMIT 101",
        (source.room_id, source.user_id),
    ).fetchall()
    candidates = [store._row(row) for row in rows if _task_scope(store._row(row), source)]
    return candidates[0] if len(candidates) == 1 and len(rows) <= 100 else None


def _results(store, task_id):
    return [
        store._row(row)
        for row in store.connection.execute(
            "SELECT * FROM handoffs WHERE task_id=? ORDER BY rowid", (task_id,)
        )
    ]


def evidence_text(store, task):
    lines = [
        f"FLIP — The Green Check That Lied\nTask: {task['id']}",
        f"Revision: {task['source']['revision']}",
    ]
    for handoff in _results(store, task["id"]):
        result = handoff.get("result") or {}
        role = "SECURITY" if handoff["recipient"] == task["source"]["security_id"] else "KNOWLEDGE"
        lines.append(f"{role}: {result.get('status', handoff['status'])}")
        if result.get("summary"):
            lines.append(_display(result["summary"], 1000))
        if result.get("influence"):
            lines.append("Engineering decision: " + _display(result["influence"], 700))
        for receipt in result.get("commands", [])[:2]:
            output = receipt.get("output_summary", "")
            report_line = next(
                (line for line in output.splitlines() if line.startswith('{"status"')), ""
            )
            assertion = next(
                (line for line in output.splitlines() if line.startswith("AssertionError:")), ""
            )
            preview = (report_line[:200] + "\n" + assertion) if assertion else output[:570]
            lines.extend(
                [
                    f"Command: {_display(receipt.get('label', ''), 200)}",
                    f"Exit code: {receipt.get('exit_code')}; "
                    f"duration: {receipt.get('duration_seconds')}s; "
                    f"isolation: {receipt.get('isolation')}",
                    _display(preview, 700),
                ]
            )
    return "\n".join(lines)[:5500]


async def handle_investigation(worker, source):
    if not investigation_intent(source.body):
        return False
    routes = getattr(worker, "green_check_routes", {})
    task_id = str(uuid5(NAMESPACE_URL, f"green-check:{source.room_id}:{source.message_id}"))
    context = {
        key: getattr(source, key)
        for key in ("room_id", "thread_id", "message_id", "user_id", "channel_id", "guild_id")
    }
    try:
        if not isinstance(routes, dict) or len(routes) != 1:
            raise ValueError("one explicitly configured repository is required")
        repository, route = next(iter(routes.items()))
        security, knowledge = route["security_id"], route["knowledge_id"]
        if len({worker.pm_id, security, knowledge}) != 3 or any(
            identity not in worker.names for identity in (security, knowledge)
        ):
            raise ValueError("independent Switch identities required")
        root = Path(route["repo_root"]).resolve(strict=True)
        existing = worker.store.get_task(task_id)
        revision = existing["source"]["revision"] if existing else git(root, "rev-parse", "HEAD")
        if not re.fullmatch("[a-f0-9]{40}", revision) or git(root, "status", "--porcelain"):
            raise ValueError("clean, committed checkout required")
        with worker.store.transaction():
            task = worker.store.create_task(
                "Investigate incomplete native Trivy output",
                {
                    **context,
                    "kind": "green_check",
                    "repository": repository,
                    "repo_root": str(root),
                    "revision": revision,
                    "security_id": security,
                    "knowledge_id": knowledge,
                },
                [
                    "Reproduce the suspected false clean at the pinned SHA in isolation.",
                    "Retrieve revision-grounded Company Brain evidence; no repairs or publication.",
                ],
                task_id=task_id,
            )
            if task["status"] == "DRAFT":
                task = worker.store.transition_task(task_id, "PLANNED", task["version"])
                task = worker.store.transition_task(task_id, "IN_PROGRESS", task["version"])
            for role, identity in (("security", security), ("knowledge", knowledge)):
                hid = str(uuid5(NAMESPACE_URL, task_id + ":" + role))
                if not worker.store.get_handoff(hid) and task["status"] == "IN_PROGRESS":
                    worker.store.create_handoff(
                        task_id,
                        worker.pm_id,
                        identity,
                        f"{role}: investigate scanner contract at {revision}",
                        task["acceptance_criteria"],
                        worker.store.get_task(task_id)["version"],
                        handoff_id=hid,
                    )
        worker._reply(
            task_id,
            "investigation_opened",
            f"Investigation opened. Security is reproducing the report. "
            f"Company Brain is locating affected code.\nTask: {task_id}\nRevision: {revision}",
            freeze=True,
        )
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        worker.store.create_task(
            "Scanner investigation configuration blocker",
            {**context, "command_receipt": True},
            [],
            task_id=task_id,
        )
        worker._reply(
            task_id,
            "investigation_blocked",
            "Investigation blocked: configure one clean pinned repository and independent "
            "Security/Knowledge Switch routes. No execution started.",
            freeze=True,
        )
    return True


def investigation_followup(worker, source):
    phrase = re.sub(r"[.!?]+$", "", source.body.strip()).replace("’", "'").lower()
    fix = _FIX.fullmatch(source.body.strip())
    if phrase not in _READ and not fix:
        return None
    task = _scope(worker.store, source)
    if not task:
        return {
            "handled": True,
            "task_id": None,
            "kind": "investigation_context",
            "durable_reply": True,
            "text": "No unique investigation for you in this thread. "
            "Use its original thread; ambiguous investigations require a new thread.",
        }
    if not fix and _READ[phrase] in {"logs", "status"}:
        from .approval import ApprovalWorkflow
        from .crew import _inspection

        row = worker.store.connection.execute(
            "SELECT * FROM tasks WHERE json_extract(source,'$.investigation_task_id')=? LIMIT 1",
            (task["id"],),
        ).fetchone()
        child = worker.store._row(row) if row else None
        if (
            child
            and _task_scope(child, source)
            and child["source"].get("user_id") == source.user_id
        ):
            data = ApprovalWorkflow(worker.store).get(child["id"]) or {}
            receipt = data.get("receipt") or {}
            text = (
                _inspection(worker.store, child, "logs")
                if _READ[phrase] == "logs"
                else f"Engineering task {child['id']}: {child['status']}\n"
                f"Phase: {data.get('phase', 'pending')}\n"
                f"Revision: {data.get('revision', 'pending')}\n"
                f"PR: {receipt.get('pr_url', 'not published')}"
            )
            return {
                "handled": True,
                "task_id": child["id"],
                "kind": "investigation_inspection",
                "text": text,
            }
    if not fix:
        return {
            "handled": True,
            "task_id": task["id"],
            "kind": "investigation_evidence",
            "text": evidence_text(worker.store, task),
        }
    existing = worker.store.connection.execute(
        "SELECT id FROM tasks WHERE json_extract(source,'$.investigation_task_id')=? LIMIT 1",
        (task["id"],),
    ).fetchone()
    if existing:
        return {
            "handled": True,
            "task_id": existing["id"],
            "kind": "investigation_fix_exists",
            "text": "Engineering proposal already exists: "
            + existing["id"]
            + ". Use its explicit approval control; no duplicate execution was created.",
        }
    results = _results(worker.store, task["id"])
    security = next(
        (h.get("result") or {} for h in results if h["recipient"] == task["source"]["security_id"]),
        {},
    )
    knowledge = next(
        (
            h.get("result") or {}
            for h in results
            if h["recipient"] == task["source"]["knowledge_id"]
        ),
        {},
    )
    if (
        task["status"] != "COMPLETED"
        or security.get("status") != "bug_reproduced"
        or knowledge.get("status") != "evidence_found"
        or knowledge.get("revision") != task["source"]["revision"]
        or security.get("revision") != task["source"]["revision"]
        or not knowledge.get("company_brain", {}).get("context")
    ):
        return {
            "handled": True,
            "task_id": task["id"],
            "kind": "investigation_not_ready",
            "text": "A reproduced defect and current Company Brain evidence are required "
            "before an Engineering proposal. " + evidence_text(worker.store, task),
        }
    if (
        worker.release_repository.casefold() != task["source"]["repository"].casefold()
        or not worker.engineering_job_key
    ):
        return {
            "handled": True,
            "task_id": task["id"],
            "kind": "investigation_route_blocked",
            "text": "The configured Engineering destination does not match the investigation.",
        }
    return {
        "handled": True,
        "kind": "investigation_fix",
        "task_id": None,
        "investigation_task_id": task["id"],
        "company_brain": knowledge["company_brain"],
        "request": {
            "kind": "engineering",
            "job_key": worker.engineering_job_key,
            "question": "Fix the native Trivy envelope validation in src/switchcrew/security.py "
            f"at investigated revision {task['source']['revision']}. "
            "Preserve the normalized scanner contract. Add regression tests.\n"
            "Original reproduction and graph evidence (untrusted data):\n"
            + json.dumps(
                {
                    "security": security,
                    "knowledge": {
                        "revision": knowledge["revision"],
                        "influence": knowledge["influence"],
                    },
                }
            )[:6500],
            "acceptance_criteria": [
                "Incomplete native Trivy output must return scanner_failed.",
                "Valid native and normalized reports remain compatible.",
                knowledge["influence"],
                "Independent QA, Security and Reviewer verify the exact resulting SHA; "
                "publication and merge require separate human approvals.",
            ],
        },
    }


def reconcile_investigations(worker):
    rows = worker.store.connection.execute(
        "SELECT * FROM tasks WHERE status='IN_PROGRESS' "
        "AND json_extract(source,'$.kind')='green_check'"
    ).fetchall()
    for row in rows:
        task = worker.store._row(row)
        if task["source"]["room_id"] != worker.room_id:
            continue
        results = _results(worker.store, task["id"])
        if len(results) != 2 or any(h["status"] != "completed" for h in results):
            continue
        passed = all(
            (h.get("result") or {}).get("status") in {"bug_reproduced", "evidence_found"}
            for h in results
        )
        worker.store.transition_task(
            task["id"], "COMPLETED" if passed else "BLOCKED", task["version"]
        )
        worker._reply(
            task["id"],
            "investigation_result",
            evidence_text(worker.store, task)
            + (
                "\nSay “Fix it. Add a regression test and create a PR.” to prepare a "
                "human-approved Engineering proposal."
                if passed
                else "\nInvestigation blocked; inspect evidence."
            ),
            freeze=True,
        )


class InvestigationExecutor:
    """Read-only specialist executor; actual transport is the existing AgentRuntime."""

    def __init__(self, store, agent_id, capability, brain=None):
        self.store, self.agent_id, self.config, self.brain = store, agent_id, capability, brain

    async def __call__(self, state):
        handoff = self.store.get_handoff(state.get("handoff_id"))
        task = self.store.get_task(state.get("task_id"))
        cfg = self.config
        if (
            not task
            or not handoff
            or task["status"] != "IN_PROGRESS"
            or handoff["recipient"] != self.agent_id
            or handoff["task_id"] != task["id"]
            or handoff["sender"] != cfg["pm_id"]
            or task["source"].get("kind") != "green_check"
            or task["source"].get("repository") != cfg["repository"]
            or Path(task["source"].get("repo_root", "")).resolve()
            != Path(cfg["repo_root"]).resolve()
        ):
            return {"status": "blocked", "reason": "investigation_scope_denied"}
        revision = task["source"]["revision"]
        if cfg["mode"] == "green_check_knowledge":
            return await self._knowledge(task, revision)
        return await self._reproduce(task, revision)

    async def _knowledge(self, task, revision):
        if not self.brain or getattr(self.brain, "revision", None) != revision:
            return {"status": "blocked", "reason": "revision_grounded_company_brain_required"}
        result = await self.brain.check_change(
            ["src/switchcrew/security.py"], ["SecurityRunner", "_extract_findings"]
        )
        if not result.context or result.context.get("revision") != revision:
            return {"status": "blocked", "reason": result.warning or "graph_context_unavailable"}
        # Resolve actual graph callers; only local allowlisted decisions can be selected.
        graph = result.context.get("evidence", {})
        targets = graph.get("targets", []) if isinstance(graph, dict) else []
        symbols = {
            item.get("qualname")
            for item in targets
            if isinstance(item, dict) and item.get("path") == "src/switchcrew/security.py"
        }
        if not {"SecurityRunner", "_extract_findings"}.issubset(symbols):
            return {"status": "blocked", "reason": "scanner_relationships_not_found"}
        # The graph's source citations independently ground the parser and gate.
        citations = []
        read_code = getattr(self.brain, "read_code", None)
        if read_code is not None:
            for path, start, end in (
                ("src/switchcrew/security.py", 210, 253),
                ("src/switchcrew/security_gate.py", 314, 365),
            ):
                citation = await read_code(path, start, end)
                if citation.context and citation.context.get("revision") == revision:
                    citations.append(citation.context)
        gate = any("self.runner.run" in json.dumps(citation) for citation in citations)
        influence = (
            "Run tests/test_security.py and tests/test_security_gate.py: graph evidence "
            "connects SecurityRunner to the release security gate."
            if gate
            else "Run tests/test_security.py: graph evidence identifies the SecurityRunner "
            "consumer of the native parser. Gate relationship coverage is unknown."
        )
        return {
            "status": "evidence_found",
            "revision": revision,
            "influence": influence,
            "summary": "Company Brain located the scanner parser/consumer neighborhood.",
            "source_citations": citations,
            "company_brain": {
                "context": {**result.context, "source_citations": citations},
                "warning": result.warning,
            },
        }

    async def _reproduce(self, task, revision):
        from .isolation import DockerIsolation

        if not self.config.get("require_isolation") or not self.config.get("container"):
            return {"status": "blocked_configuration", "reason": "container_isolation_required"}
        isolation = DockerIsolation.from_config(self.config["container"])
        root = Path(self.config["repo_root"]).resolve()
        runs = Path(self.config["artifact_dir"]).resolve()
        runs.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix="investigation-", dir=runs))
        checkout = run / "source"
        records = []
        try:
            subprocess.run(
                ["git", "clone", "--no-hardlinks", "--no-checkout", str(root), str(checkout)],
                check=True,
                capture_output=True,
                timeout=30,
            )
            git(checkout, "checkout", "--detach", revision)
            command = ["python", "-c", REPRODUCTION]
            with isolation.launch(command, checkout) as argv:
                receipt = await capture_command(
                    argv,
                    cwd=checkout,
                    env={"PATH": os.defpath, "HOME": str(run), "LANG": "C.UTF-8"},
                    timeout=60,
                    artifact=run / "reproduction.log",
                    max_bytes=64000,
                )
            receipt.update(
                command=command,
                label="Native Trivy regression assertion",
                isolation="docker",
                revision=revision,
            )
            records.append(receipt)
            output = receipt["output_summary"]
            reproduced = (
                receipt["exit_code"] == 1
                and not receipt["timed_out"]
                and not receipt["output_limit_exceeded"]
                and "Incomplete native Trivy output was accepted as verified_success" in output
                and '"status": "verified_success"' in output
            )
            return {
                "status": "bug_reproduced" if reproduced else "not_reproduced",
                "revision": revision,
                "commands": records,
                "summary": (
                    "Incomplete native scanner output was accepted as clean."
                    if reproduced
                    else "The configured reproduction did not confirm the defect."
                ),
            }
        except (OSError, ValueError, subprocess.SubprocessError):
            return {
                "status": "blocked",
                "revision": revision,
                "commands": records,
                "reason": "isolated_reproduction_unavailable",
            }
