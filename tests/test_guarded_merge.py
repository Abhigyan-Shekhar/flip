"""Controlled GitHub adapter, real Git/QA/review and durable merge safety gates."""

import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from test_approval_release import (
    GitHubDouble,
    decide,
    implement_and_review,
    release_executor,
)
from test_approval_release import (
    flow as flow,
)

from switchcrew.api import create_app
from switchcrew.merge import GuardedMerge
from switchcrew.release import ReleaseExecutor
from switchcrew.security import ScannerConfig, SecurityRunner
from switchcrew.security_gate import GateSecurityExecutor, schedule_security_gate
from switchcrew.settings import Settings
from switchcrew.store import ConflictError


class MergeGitHub(GitHubDouble):
    def __init__(self):
        super().__init__()
        self.puts = []
        self.lost_merge = False
        self.persist_merge = True
        self.check_status = "success"
        self.check_revision = None
        self.count = 1

    def handle(self, request):
        if "/statuses/" in request.url.path:
            self.posts.append(json.loads(request.content))
            return httpx.Response(201, json=json.loads(request.content))
        if request.url.path == "/graphql":
            self.posts.append(json.loads(request.content))
            self.pr["draft"] = False
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markPullRequestReadyForReview": {
                            "pullRequest": {"id": self.pr["node_id"], "isDraft": False}
                        }
                    }
                },
            )
        if request.url.path.endswith("/pulls/1"):
            return httpx.Response(200, json=self.pr)
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "total_count": self.count,
                    "check_runs": [
                        {
                            "name": "test",
                            "head_sha": self.check_revision or self.branch,
                            "status": "completed",
                            "conclusion": self.check_status,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"total_count": 0, "statuses": []})
        if request.method == "PUT":
            self.puts.append(json.loads(request.content))
            if self.persist_merge:
                self.pr.update(merged=True, state="closed", merge_commit_sha="f" * 40)
            if self.lost_merge:
                raise httpx.ReadTimeout("private-release-token")
            return httpx.Response(200, json={"merged": True, "sha": "f" * 40})
        return super().handle(request)


@pytest.fixture
async def ready(flow, tmp_path):
    revision, *_ = await implement_and_review(flow, tmp_path)
    store, _, workflow, task_id, *_ = flow
    gate = schedule_security_gate(
        store,
        task_id,
        "pm",
        "security",
        {"block_at_or_above": "high", "allowed_repo_roots": [str(tmp_path)]},
    )
    handoff_id = gate["security_handoff_id"]
    store.accept_handoff(handoff_id, "security")
    # Controlled scanner contract executed as a real subprocess; not live scanner acceptance.
    runner = SecurityRunner(
        [
            ScannerConfig(
                "controlled-static-scanner",
                (sys.executable, "-c", "print('{\"findings\": []}')"),
                version="fixture-v1",
                category="static",
            )
        ]
    )
    report = await GateSecurityExecutor(store, "security", runner)(
        {"task_id": gate["security_task_id"], "handoff_id": handoff_id}
    )
    assert report["status"] == "verified_success"
    store.complete_handoff(handoff_id, "security", report, "security")
    decide(flow, "release")
    publisher = MergeGitHub()
    executor, state = release_executor(flow, publisher)
    assert (await executor(state))["status"] == "completed"
    publisher.pr.update(
        number=1, draft=False, merged=False, mergeable=True, mergeable_state="clean"
    )
    publisher.pr["base"]["sha"] = flow[-1]
    return GuardedMerge(flow[2], publisher, ["test"]), publisher, flow[3], revision


async def test_published_checks_are_bound_to_actual_independent_receipts(ready, flow):
    service, publisher, task, revision = ready
    data = flow[2].get(task)
    executor = ReleaseExecutor(flow[2], flow[1], "release", publisher, publish_checks=True)
    before = len(publisher.posts)
    await executor._publish_checks(task, data, publisher.pr)
    checks = publisher.posts[before:]
    assert [item["context"] for item in checks] == ["FLIP / QA", "FLIP / Security", "FLIP / Review"]
    assert all(item["state"] == "success" for item in checks)
    assert data["qa_handoff"] in checks[0]["description"]
    assert data["review_handoff"] in checks[2]["description"]
    flow[0].connection.execute("UPDATE security_gates SET status='failed'")
    before = len(publisher.posts)
    with pytest.raises(PermissionError):
        await executor._publish_checks(task, data, publisher.pr)
    assert len(publisher.posts) == before


async def test_human_can_mark_verified_pr_ready_without_authorizing_merge(ready):
    service, publisher, task, revision = ready
    publisher.pr.update(draft=True, node_id="PR_test")
    before = len(publisher.posts)
    with pytest.raises(PermissionError):
        await service.mark_ready(task, "agent", revision)
    with pytest.raises(PermissionError):
        await service.mark_ready(task, "owner", "b" * 40)
    assert len(publisher.posts) == before
    result = await service.mark_ready(task, "owner", revision)
    assert result["ready"] and publisher.pr["draft"] is False
    assert publisher.posts[-1]["variables"]["input"] == {
        "pullRequestId": "PR_test",
        "clientMutationId": f"{task}:{revision}",
    }
    assert service.get(task)["phase"] == "awaiting_approval"
    with pytest.raises(PermissionError):
        await service.execute(task, "owner", revision)
    assert not publisher.puts


@pytest.mark.parametrize("change", ["head", "base", "security"])
async def test_ready_refuses_changed_or_unverified_evidence(ready, flow, change):
    service, publisher, task, revision = ready
    publisher.pr.update(draft=True, node_id="PR_test")
    if change == "head":
        publisher.pr["head"]["sha"] = "b" * 40
    elif change == "base":
        publisher.pr["base"]["sha"] = "b" * 40
    else:
        flow[0].connection.execute("UPDATE security_gates SET status='failed'")
    before = len(publisher.posts)
    with pytest.raises(PermissionError):
        await service.mark_ready(task, "owner", revision)
    assert len(publisher.posts) == before
    assert publisher.pr["draft"] is True


async def test_merge_requires_separate_human_approval_and_exact_checks(ready, flow):
    service, publisher, task, revision = ready
    with pytest.raises(PermissionError):
        await service.execute(task, "owner", revision)
    with pytest.raises(PermissionError):
        await service.approve(task, "agent", 0, revision)
    approval = await service.approve(task, "owner", 0, revision)
    assert approval["version"] == 1
    with pytest.raises(ConflictError):
        await service.approve(task, "owner", 0, revision)
    receipt = await service.execute(task, "owner", revision)
    assert receipt["status"] == "merged"
    assert publisher.puts == [{"sha": revision, "merge_method": "merge"}]
    assert flow[0].get_task(task)["status"] == "COMPLETED"
    assert await service.execute(task, "owner", revision) == receipt
    assert len(publisher.puts) == 1
    assert flow[2].get(task)["merge_receipt"] == receipt


@pytest.mark.parametrize(
    "failure", ["head", "base", "check", "stale_check", "draft", "unknown", "pagination"]
)
async def test_remote_changes_block_merge_after_approval(ready, failure):
    service, publisher, task, revision = ready
    await service.approve(task, "owner", 0, revision)
    if failure == "head":
        publisher.pr["head"]["sha"] = "b" * 40
    elif failure == "base":
        publisher.pr["base"]["sha"] = "b" * 40
    elif failure == "check":
        publisher.check_status = "failure"
    elif failure == "stale_check":
        publisher.check_revision = "b" * 40
    elif failure == "draft":
        publisher.pr["draft"] = True
    elif failure == "unknown":
        publisher.pr["mergeable"] = None
    else:
        publisher.count = 101
    with pytest.raises(PermissionError):
        await service.execute(task, "owner", revision)
    assert not publisher.puts
    if failure == "head":
        assert service.get(task)["phase"] == "stale"
        assert service.get(task)["approval"] is None


@pytest.mark.parametrize("persist", [False, True])
async def test_lost_merge_response_never_repeats_mutation_after_restart(ready, persist):
    service, publisher, task, revision = ready
    await service.approve(task, "owner", 0, revision)
    publisher.lost_merge, publisher.persist_merge = True, persist
    with pytest.raises(httpx.ReadTimeout):
        await service.execute(task, "owner", revision)
    restarted = GuardedMerge(service.workflow, publisher, ["test"])
    result = await restarted.execute(task, "owner", revision)
    assert result["status"] == ("merged" if persist else "pending_reconciliation")
    assert len(publisher.puts) == 1


async def test_changed_internal_qa_evidence_blocks_merge(ready, flow):
    service, publisher, task, revision = ready
    await service.approve(task, "owner", 0, revision)
    qa = flow[0].get_handoff(flow[2].get(task)["qa_handoff"])
    qa["result"]["tested_revision"] = "b" * 40
    flow[0].connection.execute(
        "UPDATE handoffs SET result=? WHERE id=?", (json.dumps(qa["result"]), qa["id"])
    )
    with pytest.raises(PermissionError):
        await service.execute(task, "owner", revision)
    assert not publisher.puts


def test_merge_api_disabled_and_rejects_service_identity_and_actor_spoofing(tmp_path):
    service_token, human_token = "s" * 32, "h" * 32
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.db",
        api_token=service_token,
        approval_credentials={"owner": human_token},
    )
    with TestClient(create_app(settings)) as client:
        path = "/tasks/nonexistent/merge-approval"
        body = {"revision": "a" * 40, "expected_version": 0}
        assert (
            client.post(
                path, headers={"Authorization": "Bearer " + service_token}, json=body
            ).status_code
            == 403
        )
        assert (
            client.post(
                path,
                headers={"Authorization": "Bearer " + human_token},
                json={**body, "actor": "owner"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                path, headers={"Authorization": "Bearer " + human_token}, json=body
            ).status_code
            == 503
        )


def test_required_checks_cannot_be_omitted(flow):
    with pytest.raises(ValueError):
        GuardedMerge(flow[2], MergeGitHub(), [])


async def test_enabled_api_uses_human_identity_for_merge(ready, tmp_path):
    _, publisher, task, revision = ready
    service_token, human_token = "s" * 32, "h" * 32
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "application.db",
        api_token=service_token,
        approval_credentials={"owner": human_token},
        merge_enabled=True,
        merge_github_token="private-merge-credential",
        merge_required_checks=["test"],
        release_repository="org/repo",
        release_base="master",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.guarded_merge.publisher = publisher
        service = {"Authorization": "Bearer " + service_token}
        human = {"Authorization": "Bearer " + human_token}
        path = f"/tasks/{task}"
        result = client.get(path + "/pull-request", headers=service)
        assert result.status_code == 200
        assert result.json()["merge"]["version"] == 0
        decision = {"revision": revision, "expected_version": 0}
        assert (
            client.post(path + "/merge-approval", headers=service, json=decision).status_code == 403
        )
        result = client.post(path + "/merge-approval", headers=human, json=decision)
        assert result.status_code == 200
        assert result.json()["approval"]["actor"] == "owner"
        assert (
            client.post(path + "/merge", headers=service, json={"revision": revision}).status_code
            == 403
        )
        result = client.post(path + "/merge", headers=human, json={"revision": revision})
        assert result.status_code == 200
        assert result.json()["status"] == "merged"
        assert len(publisher.puts) == 1


@pytest.mark.parametrize("failure", ["missing", "pending", "stale", "same_identity"])
async def test_merge_requires_passing_independent_security_even_without_project_policy(
    ready, flow, failure
):
    service, publisher, task, revision = ready
    await service.approve(task, "owner", 0, revision)
    store = flow[0]
    if failure == "missing":
        store.connection.execute("DELETE FROM security_gates WHERE task_id=?", (task,))
    elif failure == "pending":
        store.connection.execute(
            "UPDATE security_gates SET status='pending' WHERE task_id=?", (task,)
        )
    elif failure == "stale":
        store.connection.execute(
            "UPDATE security_gates SET revision=? WHERE task_id=?", ("b" * 40, task)
        )
    else:
        store.connection.execute(
            "UPDATE security_gates SET security_recipient='engineering' WHERE task_id=?", (task,)
        )
    with pytest.raises(PermissionError):
        await service.execute(task, "owner", revision)
    assert not publisher.puts
