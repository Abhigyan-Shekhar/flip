"""Real Git/QA and durable approvals; GitHub/Console calls are controlled doubles."""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_engineering_qa import ControlledSession, commit, receipt
from test_workers import Transport, event

from switchcrew.api import create_app
from switchcrew.approval import ApprovalWorkflow
from switchcrew.discord import DiscordBinding
from switchcrew.engineering import EngineeringExecutor, Ledger, assignment_id, git
from switchcrew.qa import QAExecutor
from switchcrew.release import GitHubPublisher, ReleaseExecutor
from switchcrew.settings import Settings
from switchcrew.store import ConflictError, InvalidTransitionError, Store
from switchcrew.workers import Worker


@pytest.fixture
def flow(tmp_path):
    store = Store(tmp_path / "application.db")
    ledger = Ledger(tmp_path / "ledger.db")
    workflow = ApprovalWorkflow(store, ["owner"])
    task = store.create_task(
        "Correct answer.txt", {"room_id": "room", "thread_id": "thread"}, ["Answer equals correct"]
    )
    workflow.request(task["id"], "pm", "engineering", "qa", "release", "fix", "org/repo", "master")
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    base = commit(source, "broken")
    yield store, ledger, workflow, task["id"], source, base
    store.close()
    ledger.close()


def decide(flow, stage="engineering", approved=True, actor="owner", version=None):
    store, _, workflow, task_id, *_ = flow
    return workflow.decide(
        task_id,
        stage,
        approved,
        actor,
        store.get_task(task_id)["version"] if version is None else version,
    )


def handoff_state(store, identity, role):
    h = store.get_handoff(identity)
    return {
        "agent_role": role,
        "task_id": h["task_id"],
        "handoff_id": identity,
        "question": h["question"],
        "acceptance_criteria": h["acceptance_criteria"],
    }


async def implement_and_review(flow, tmp_path, correct=True):
    store, ledger, workflow, task_id, source, base = flow
    data = decide(flow)
    identity = data["engineering_handoff"]
    state = handoff_state(store, identity, "engineering")
    session = ControlledSession()
    executor = EngineeringExecutor(
        ledger,
        tmp_path / "engineering",
        source,
        base,
        session,
        authorize=lambda state: workflow.require_engineering(state, "engineering"),
    )
    store.accept_handoff(identity, "engineering")
    assert (await executor(state))["status"] == "pending_execution"
    final = commit(session.checkout, "correct" if correct else "still broken")
    receipt(session.checkout, assignment_id(identity), final)
    result = await executor(state)
    store.complete_handoff(identity, "engineering", result, "engineering")
    data = workflow.reconcile(task_id)
    qa_id = data["qa_handoff"]
    store.accept_handoff(qa_id, "qa")
    commands = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'",
        ]
    ]
    qa = QAExecutor(ledger, tmp_path / "qa", assignment_id(identity), final, commands)
    qa_result = await qa(handoff_state(store, qa_id, "qa"))
    store.complete_handoff(qa_id, "qa", qa_result, "qa")
    workflow.reconcile(task_id)
    return final, session, qa_result


class GitHubDouble(GitHubPublisher):
    def __init__(self, lost=False, absent=False):
        self.posts, self.pushes, self.branch, self.pr = [], [], None, None
        self.lost, self.absent = lost, absent
        super().__init__(
            "org/repo",
            "master",
            "private-release-token",
            transport=httpx.MockTransport(self.handle),
        )

    async def push(self, checkout, revision, branch):
        self.pushes.append((checkout, revision, branch))
        self.branch = revision

    def handle(self, request):
        if "/git/ref/" in request.url.path:
            if self.branch:
                return httpx.Response(200, json={"object": {"sha": self.branch}})
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, json=[self.pr] if self.pr else [])
        body = json.loads(request.content)
        self.posts.append(body)
        result = {
            "state": "open",
            "draft": body["draft"],
            "head": {"sha": self.branch, "ref": body["head"], "repo": {"full_name": "org/repo"}},
            "base": {"ref": body["base"], "repo": {"full_name": "org/repo"}},
            "html_url": "https://github.com/org/repo/pull/1",
        }
        if not self.absent:
            self.pr = result
        if self.lost:
            raise httpx.ReadTimeout("private-release-token")
        return httpx.Response(201, json=result)


def release_executor(flow, publisher):
    store, ledger, workflow, task_id, *_ = flow
    data = workflow.get(task_id)
    state = handoff_state(store, data["release_handoff"], "release")
    return ReleaseExecutor(workflow, ledger, "release", publisher), state


@pytest.mark.asyncio
async def test_full_approved_pipeline_real_git_qa_and_draft_receipt(flow, tmp_path):
    store, _, workflow, task_id, *_ = flow
    assert not store.connection.execute("SELECT * FROM handoffs").fetchall()
    final, _, qa = await implement_and_review(flow, tmp_path)
    assert qa["status"] == "verified_success"
    assert workflow.get(task_id)["phase"] == "human_review"
    assert not [o for o in store.pending_outbox() if o["recipient"] == "release"]
    decide(flow, "release")
    publisher = GitHubDouble()
    executor, state = release_executor(flow, publisher)
    result = await executor(state)
    assert result["status"] == "completed"
    recorded = result["receipt"]
    assert recorded["commit_sha"] == final
    assert recorded["approval"]["actor"] == "owner"
    assert recorded["tests"] == qa["commands"]
    assert recorded["pr_url"] == "https://github.com/org/repo/pull/1"
    assert publisher.posts[0]["draft"] is True
    assert store.get_task(task_id)["status"] == "PR_OPEN"
    assert (await executor(state)) == result
    assert len(publisher.posts) == len(publisher.pushes) == 1
    assert "private-release-token" not in json.dumps(result)
    assert any(o["payload"]["type"] == "release_receipt" for o in store.pending_outbox())


@pytest.mark.asyncio
async def test_unapproved_or_forged_handoff_cannot_start_console(flow, tmp_path):
    store, ledger, workflow, task_id, source, base = flow
    session = ControlledSession()
    # An arbitrary handoff in SQLite is not an approval capability.
    h = store.create_handoff(task_id, "pm", "engineering", "Bad", ["Bad"], 1)
    executor = EngineeringExecutor(
        ledger,
        tmp_path / "work",
        source,
        base,
        session,
        authorize=lambda state: workflow.require_engineering(state, "engineering"),
    )
    assert (await executor(handoff_state(store, h["id"], "engineering")))["status"] == "blocked"
    assert session.creates == session.deliveries == 0
    assert not (tmp_path / "work").exists()
    with pytest.raises(PermissionError):
        decide(flow, actor="pm")
    assert not workflow.get(task_id).get("engineering_approval")


@pytest.mark.asyncio
async def test_failed_qa_and_rejection_block_release(flow, tmp_path):
    store, ledger, workflow, task_id, *_ = flow
    await implement_and_review(flow, tmp_path, correct=False)
    assert store.get_task(task_id)["status"] == "FAILED"
    assert workflow.get(task_id)["phase"] == "failed"
    with pytest.raises(ConflictError):
        decide(flow, "release")
    publisher = GitHubDouble()
    result = await ReleaseExecutor(workflow, ledger, "release", publisher)(
        {"task_id": task_id, "handoff_id": "forged", "question": "Publish"}
    )
    assert result["status"] == "blocked"
    assert not publisher.posts and not publisher.pushes


@pytest.mark.parametrize("stage", ["engineering", "release"])
@pytest.mark.asyncio
async def test_reject_is_durable_and_never_creates_privileged_handoff(flow, tmp_path, stage):
    store, _, workflow, task_id, *_ = flow
    if stage == "release":
        await implement_and_review(flow, tmp_path)
    decide(flow, stage, False)
    assert store.get_task(task_id)["status"] == "CANCELLED"
    assert ApprovalWorkflow(store).get(task_id)["phase"] == "rejected"
    with pytest.raises(ConflictError):
        decide(flow, stage)
    assert not [
        o
        for o in store.pending_outbox()
        if o["recipient"] == ("engineering" if stage == "engineering" else "release")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("absent", [False, True])
async def test_uncertain_pr_creation_reopens_ledger_without_duplicate_post(flow, tmp_path, absent):
    _, ledger, workflow, task_id, *_ = flow
    await implement_and_review(flow, tmp_path)
    decide(flow, "release")
    publisher = GitHubDouble(lost=True, absent=absent)
    executor, state = release_executor(flow, publisher)
    assert (await executor(state))["status"] == "pending_execution"
    reopened = Ledger(tmp_path / "ledger.db")
    try:
        executor.ledger = reopened
        again = await executor(state)
        assert again["status"] == ("pending_execution" if absent else "completed")
        assert len(publisher.posts) == 1
        assert len(publisher.pushes) == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_stale_revision_and_changed_target_cannot_publish(flow, tmp_path):
    _, _, _, _, *_ = flow
    await implement_and_review(flow, tmp_path)
    decide(flow, "release")
    publisher = GitHubDouble()
    executor, state = release_executor(flow, publisher)
    publisher.repository = "attacker/repo"
    assert (await executor(state))["status"] == "blocked"
    publisher.repository = "org/repo"
    data = flow[2].get(flow[3])
    implementation = flow[1].get(assignment_id(data["engineering_handoff"]))["result"]
    commit(Path(implementation["checkout"]), "changed after approval")
    result = await executor(state)
    assert result["status"] == "stale_qa"
    assert not publisher.posts and not publisher.pushes
    store, _, workflow, task_id, *_ = flow
    store.accept_handoff(state["handoff_id"], "release")
    store.complete_handoff(state["handoff_id"], "release", result, "release")
    workflow.reconcile(task_id)
    assert store.get_task(task_id)["status"] == "FAILED"


@pytest.mark.asyncio
async def test_cancellation_before_publication_and_during_claim(flow, tmp_path):
    store, _, workflow, task_id, *_ = flow
    await implement_and_review(flow, tmp_path)
    decide(flow, "release")
    executor, state = release_executor(flow, GitHubDouble())
    workflow.start_release(state, "release")
    assert store.get_task(task_id)["status"] == "PUBLISHING"
    with pytest.raises(InvalidTransitionError):
        store.transition_task(task_id, "CANCELLED", store.get_task(task_id)["version"])
    assert (await executor(state))["status"] == "completed"


def test_approval_atomic_rollback_on_handoff_failure(flow, monkeypatch):
    store, _, workflow, task_id, *_ = flow
    before = store.get_task(task_id)

    def fail(*args, **kwargs):
        raise RuntimeError("crash before handoff commit")

    monkeypatch.setattr(store, "create_handoff", fail)
    with pytest.raises(RuntimeError):
        decide(flow)
    assert store.get_task(task_id) == before
    assert workflow.get(task_id)["phase"] == "awaiting_engineering_approval"
    assert not [e for e in store.list_events(task_id) if e["type"] == "engineering.decision"]


@pytest.mark.asyncio
async def test_pm_discord_engineering_route_and_authenticated_same_thread_decision(tmp_path):
    store = Store(tmp_path / "pm.db")
    pm = Worker(
        "pm",
        store,
        Transport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_guilds=["guild"],
        allowed_users=["owner"],
        engineering_id="engineering",
        qa_id="qa",
        release_id="release",
        approval_users=["owner"],
        release_repository="org/repo",
        release_base="master",
    )
    request = {
        "kind": "engineering",
        "job_key": "fix",
        "question": "Fix answer",
        "acceptance_criteria": ["Tests pass"],
        "approved": True,
        "actor": "owner",
    }
    await pm.receive(event("human", json.dumps(request)))
    await pm.process_pending()
    task = store._row(store.connection.execute("SELECT * FROM tasks").fetchone())
    assert task["status"] == "AWAITING_APPROVAL"
    assert not store.connection.execute("SELECT * FROM handoffs").fetchall()
    decision = {
        "type": "approval",
        "task_id": task["id"],
        "stage": "engineering",
        "approved": True,
        "expected_version": task["version"],
        "actor": "spoof",
    }
    forged = event("attacker", json.dumps(decision), "a1")
    await pm.receive(forged)
    wrong_thread = event("human", json.dumps(decision), "a2")
    wrong_thread["payload"]["thread_id"] = "other"
    await pm.receive(wrong_thread)
    await pm.process_pending()
    assert store.get_task(task["id"])["status"] == "AWAITING_APPROVAL"
    await pm.receive(event("human", json.dumps(decision), "a3"))
    await pm.process_pending()
    await pm.flush_outbox()
    h = store._row(store.connection.execute("SELECT * FROM handoffs").fetchone())
    assert h["recipient"] == "engineering" and h["sender"] == "pm"
    assert pm.approvals.get(task["id"])["engineering_approval"]["actor"] == "owner"
    store.close()


def test_api_separate_human_credential_version_replay_and_lifecycle_bypass(tmp_path):
    service = "service-token-" + "s" * 32
    human = "human-token-" + "h" * 32
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.db",
        api_token=service,
        approval_credentials={"owner": human},
        pm_agent_id="pm",
        engineering_agent_id="engineering",
        qa_agent_id="qa",
        release_agent_id="release",
        release_repository="org/repo",
        release_base="master",
    )
    service_headers = {"Authorization": "Bearer " + service}
    human_headers = {"Authorization": "Bearer " + human}
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/tasks",
            headers=service_headers,
            json={
                "kind": "engineering",
                "job_key": "fix",
                "objective": "Fix",
                "acceptance_criteria": ["Pass"],
            },
        )
        assert response.status_code == 201
        task = response.json()
        path = f"/tasks/{task['id']}"
        decision = {"stage": "engineering", "approved": True, "expected_version": task["version"]}
        assert (
            client.post(path + "/approvals", headers=service_headers, json=decision).status_code
            == 403
        )
        assert client.post(path + "/approvals", json=decision).status_code == 403
        assert (
            client.post(
                path + "/transitions",
                headers=service_headers,
                json={"status": "IN_PROGRESS", "expected_version": task["version"]},
            ).status_code
            == 409
        )
        assert (
            client.post(
                path + "/approvals", headers=human_headers, json={**decision, "actor": "someone"}
            ).status_code
            == 422
        )
        assert (
            client.post(path + "/approvals", headers=human_headers, json=decision).status_code
            == 200
        )
        assert (
            client.post(path + "/approvals", headers=human_headers, json=decision).status_code
            == 409
        )
        assert (
            client.get(path + "/workflow", headers=service_headers).json()["phase"] == "engineering"
        )
        assert (
            client.post(
                path + "/approvals",
                headers=human_headers,
                json={"stage": "release", "approved": True, "expected_version": task["version"]},
            ).status_code
            == 409
        )
    settings.approval_credentials = {"owner": settings.api_token}
    with pytest.raises(RuntimeError, match="distinct"):
        create_app(settings)


@pytest.mark.asyncio
async def test_publication_uses_fresh_bare_repo_and_only_approved_sha(flow, tmp_path, monkeypatch):
    _, _, _, _, source, base = flow
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    calls = []
    original = subprocess.run

    def run(args, **kwargs):
        if "push" in args:
            calls.append((args, kwargs))
            args = [str(remote) if a == "https://github.com/org/repo.git" else a for a in args]
        return original(args, **kwargs)

    class Local(GitHubPublisher):
        async def branch_revision(self, branch):
            try:
                return git(remote, "rev-parse", "--verify", "refs/heads/" + branch)
            except subprocess.CalledProcessError:
                return None

    monkeypatch.setattr(subprocess, "run", run)
    publisher = Local("org/repo", "master", "secret")
    await publisher.push(source, base, "codex/test")
    await publisher.push(source, base, "codex/test")
    assert len(calls) == 1
    args, options = calls[0]
    assert "secret" not in " ".join(args)
    assert "secret" not in str(options["env"].get("PATH"))
    assert args[2] != str(source)
    assert options["env"]["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert git(remote, "rev-parse", "refs/heads/codex/test") == base


@pytest.mark.asyncio
async def test_configured_runtime_engineering_qa_release_delivery(flow, tmp_path, monkeypatch):
    from switchcrew import engineering_qa
    from switchcrew.graphs import build_agent_graph
    from switchcrew.runtime import AgentConnection, AgentRuntime

    store, ledger, workflow, task_id, source, base = flow
    data = decide(flow)
    session, publisher = ControlledSession(), GitHubDouble()
    monkeypatch.setattr(engineering_qa, "ConsoleCodex", lambda *a, **kw: session)
    monkeypatch.setattr(engineering_qa, "GitHubPublisher", lambda *a, **kw: publisher)

    def runtime(role, jobs):
        connection = AgentConnection(
            role,
            role,
            "participant-" + role,
            role,
            "private",
            "room",
            allowed_senders={"participant-pm": "pm", "participant-engineering": "engineering"}
            if role != "engineering"
            else {"participant-pm": "pm", "participant-qa": "qa"},
            target_names={"pm": "pm", "qa": "qa", "engineering": "engineering"},
        )
        config = {
            "checkout_root": str(tmp_path / role),
            "control_file": "unused",
            "console_database": "unused",
            "job_templates": {"fix": jobs},
            "release": {"repository": "org/repo", "base": "master", "token": "private"},
        }
        switch = Transport()
        execute = engineering_qa.configured_executor(
            config, connection, switch, store, ledger, workflow
        )
        return AgentRuntime(connection, store, switch, build_agent_graph(role, executor=execute))

    async def deliver(runtime, identity, sender):
        h = store.get_handoff(identity)
        body = {
            "type": "handoff",
            "handoff_id": identity,
            "task_id": task_id,
            "question": h["question"],
            "acceptance_criteria": h["acceptance_criteria"],
        }
        await runtime.receive(event("participant-" + sender, json.dumps(body), identity))
        await runtime.process_pending()

    engineering = runtime(
        "engineering",
        {
            "source": str(source),
            "base_revision": base,
            "console_agent_id": "codex",
            "codex_name": "codex",
            "codex_switch_agent_id": "codex",
        },
    )
    await deliver(engineering, data["engineering_handoff"], "pm")
    assert store.get_handoff(data["engineering_handoff"])["status"] == "accepted"
    assert len(store.pending_inbox("engineering")) == 1
    final = commit(session.checkout, "correct")
    receipt(session.checkout, assignment_id(data["engineering_handoff"]), final)
    await engineering.process_pending()
    data = workflow.reconcile(task_id)
    qa = runtime(
        "qa",
        {
            "commands": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('answer.txt').read_text() == 'correct'",
                ]
            ]
        },
    )
    await deliver(qa, data["qa_handoff"], "engineering")
    data = workflow.reconcile(task_id)
    assert data["phase"] == "human_review"
    data = decide(flow, "release")
    release = runtime("release", {})
    await deliver(release, data["release_handoff"], "pm")
    assert store.get_handoff(data["release_handoff"])["status"] == "completed"
    assert workflow.get(task_id)["receipt"]["commit_sha"] == final
    assert len(publisher.posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["engineering", "qa", "release"])
async def test_configured_capability_blocks_arbitrary_legacy_jobs_without_approval(flow, role):
    from types import SimpleNamespace

    from switchcrew.engineering_qa import configured_executor

    store, ledger, workflow, *_ = flow
    task = store.create_task("Unapproved", {}, ["Anything"])
    execute = configured_executor(
        {"jobs": {"arbitrary": {"commands": [["anything"]]}}},
        SimpleNamespace(role=role, agent_id=role),
        None,
        store,
        ledger,
        workflow,
    )
    assert (await execute({"task_id": task["id"], "handoff_id": "arbitrary"}))[
        "status"
    ] == "blocked"


@pytest.mark.asyncio
async def test_pm_graph_routes_engineering_to_approval_without_execution():
    from switchcrew.graphs import build_agent_graph, invoke_agent_graph

    state = {
        "agent_role": "pm",
        "task_id": "test",
        "handoff_id": "",
        "question": "Fix",
        "acceptance_criteria": ["Pass"],
        "requested_role": "engineering",
    }
    result = await invoke_agent_graph(build_agent_graph("pm"), state)
    assert result["result"]["status"] == "awaiting_approval"
    assert result["result"]["selected_role"] == "engineering"
