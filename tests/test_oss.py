import hashlib
import hmac
import json
import subprocess

import pytest

from switchcrew.approval import ApprovalWorkflow
from switchcrew.oss import OSSMaintainer
from switchcrew.store import Store
from switchcrew.triggers import WebhookRejected

SECRET = b"oss maintainer github webhook secret at least 32 bytes"
ROUTE = {
    "qa": "qa-agent",
    "reviewer": "reviewer-agent",
    "release": "release-agent",
    "job_key": "fix",
    "base": "main",
}


def local_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    (path / "README.md").write_text("# Fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    return path.resolve()


def payload(number, title, body, repo="owner/project", *, actor="community-user", action="opened"):
    return {
        "action": action,
        "repository": {"full_name": repo},
        "sender": {"login": actor},
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "updated_at": f"2026-11-03T10:{number % 60:02}:00Z",
            "html_url": "https://attacker.invalid/ignored",
            "user": {"login": actor},
            "labels": [{"name": "bug"}],
        },
    }


def signed(body_payload, delivery="delivery-1", event="issues", secret=SECRET):
    body = json.dumps(body_payload, separators=(",", ":")).encode()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={signature}",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
    }, body


def maintainer(store, root, *, repo_options=None, **kwargs):
    return OSSMaintainer(
        store,
        "pm-agent",
        {"research": "research-agent", "engineering": "engineering-agent"},
        {
            "owner/project": {
                "repo_root": str(root),
                "base": "main",
                **(repo_options or {}),
            }
        },
        ROUTE,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_signed_issue_starts_research_handoff_through_existing_orchestrator(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(
        store,
        root,
        source_context={"switch_room_id": "oss-room", "discord_thread_id": "thread-1"},
        repo_options={
            "project_id": "switchcrew-project",
            "source_mapping": {"owner_team": "maintainers"},
        },
    )
    headers, body = signed(
        payload(
            17, "Crash when saving preferences", "Saving preferences crashes after profile update."
        )
    )
    result = await service.accept_event(headers, body, secret=SECRET)
    assert result["status"] == "accepted"
    assert result["reproduction_status"] == "not_run"
    task = store.get_task(result["task_id"])
    assert task["status"] == "IN_PROGRESS"
    assert task["source"]["oss"]["issue_number"] == 17
    assert task["source"]["oss"]["operator_context"]["discord_thread_id"] == "thread-1"
    assert task["source"]["project_id"] == "switchcrew-project"
    assert task["source"]["owner_team"] == "maintainers"
    assert task["source"]["room_id"] == "oss-room"
    assert task["source"]["thread_id"] == "thread-1"
    assert "untrusted evidence" in task["objective"]
    assert "attacker.invalid" not in task["source"]["oss"]["issue_url"]
    handoff = store.get_handoff(result["research_handoff_id"])
    assert handoff["recipient"] == "research-agent"
    assert handoff["sender"] == "pm-agent"
    assert handoff["acceptance_criteria"][0].startswith("Research")
    plan = service._orchestrators["owner/project"].get(task["id"])
    assert [step["role"] for step in plan["spec"]["steps"]] == ["research", "engineering"]
    store.close()


@pytest.mark.asyncio
async def test_duplicate_issue_and_delivery_survive_restart_without_duplicate_tasks(tmp_path):
    root = local_repo(tmp_path / "repo")
    path = tmp_path / "store.sqlite"
    store = Store(path)
    headers, body = signed(
        payload(3, "A visible rendering failure", "The visible page fails to render."),
        "gh-delivery-a",
    )
    first = await maintainer(store, root).accept_event(headers, body, secret=SECRET)
    assert first["status"] == "accepted"
    store.close()

    reopened = Store(path)
    service = maintainer(reopened, root)
    duplicate_headers, duplicate_body = signed(
        payload(3, "A visible rendering failure", "The visible page fails to render."),
        "gh-delivery-a",
    )
    duplicate = await service.accept_event(duplicate_headers, duplicate_body, secret=SECRET)
    assert duplicate["status"] == "duplicate"
    assert duplicate["task_id"] == first["task_id"]
    changed_delivery_headers, changed_delivery_body = signed(
        payload(3, "A visible rendering failure", "The visible page fails to render."),
        "different-delivery",
    )
    semantic_duplicate = await service.accept_event(
        changed_delivery_headers, changed_delivery_body, secret=SECRET
    )
    assert semantic_duplicate["status"] == "duplicate"
    assert (
        reopened.connection.execute(
            "select count(*) from tasks where json_extract(source,'$.oss.issue_number')=3 "
            "and json_extract(source,'$.parent_task_id') IS NULL"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_replay_recovers_if_process_stops_after_task_commit_before_plan_start(
    tmp_path, monkeypatch
):
    from switchcrew.orchestration import Orchestrator

    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root)
    headers, body = signed(payload(7, "Investigate a visible error", "The page shows an error."))
    original = Orchestrator.start

    def interrupted(self, task_id):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(Orchestrator, "start", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await service.accept_event(headers, body, secret=SECRET)
    task_id = next(
        row[0]
        for row in store.connection.execute(
            "select id from tasks where json_extract(source,'$.oss.issue_number')=7 "
            "and json_extract(source,'$.parent_task_id') IS NULL"
        )
    )
    assert store.get_task(task_id)["status"] == "DRAFT"
    monkeypatch.setattr(Orchestrator, "start", original)
    replayed = await service.accept_event(headers, body, secret=SECRET)
    assert replayed["task_id"] == task_id
    assert replayed["research_handoff_id"]
    assert store.get_task(task_id)["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_webhook_auth_repo_event_and_self_origin_are_enforced(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root, self_bot_logins=["switchcrew-bot"])
    headers, body = signed(payload(2, "Issue", "Body"))
    bad_signature = dict(headers, **{"X-Hub-Signature-256": "sha256=" + "0" * 64})
    with pytest.raises(WebhookRejected, match="signature_invalid"):
        await service.accept_event(bad_signature, body, secret=SECRET)
    untrusted_repo_headers, untrusted_repo_body = signed(
        payload(2, "Issue", "Body", repo="attacker/repo")
    )
    with pytest.raises(WebhookRejected, match="repository_not_authorized"):
        await service.accept_event(untrusted_repo_headers, untrusted_repo_body, secret=SECRET)
    wrong_event_headers, wrong_event_body = signed(payload(2, "Issue", "Body"), event="push")
    with pytest.raises(WebhookRejected, match="event_type_not_authorized"):
        await service.accept_event(wrong_event_headers, wrong_event_body, secret=SECRET)
    bot_headers, bot_body = signed(payload(2, "Issue", "Body", actor="switchcrew-bot"))
    ignored = await service.accept_event(bot_headers, bot_body, secret=SECRET)
    assert ignored == {"status": "ignored", "reason": "self_origin_event"}
    assert store.connection.execute("select count(*) from tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_issue_edits_add_evidence_without_rewriting_objective(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root)
    headers, body = signed(payload(21, "Crash in preference save", "Original evidence."))
    initial = await service.accept_event(headers, body, secret=SECRET)
    task_before = store.get_task(initial["task_id"])
    initial_event_count = len(store.list_events(initial["task_id"]))
    revised_payload = payload(21, "Changed title", "New reproduction evidence.", action="edited")
    edit_headers, edit_body = signed(revised_payload, "edit-delivery", event="issues")
    update = await service.accept_event(edit_headers, edit_body, secret=SECRET)
    task_after = store.get_task(initial["task_id"])
    assert update["status"] == "updated_evidence"
    assert update["objective_changed"] is False
    assert task_after["objective"] == task_before["objective"]
    assert task_after["source"] == task_before["source"]
    event = store.list_events(initial["task_id"])[-1]
    assert event["type"] == "oss.issue.evidence_updated"
    assert (
        event["details"]["body_sha256"]
        != task_before["source"]["oss"]["issue_snapshot"]["body_sha256"]
    )
    pm_outbox = [
        row
        for row in store.pending_outbox()
        if row["recipient"] == "pm-agent" and row["payload"]["type"] == "oss.issue.evidence_updated"
    ]
    assert len(pm_outbox) == 1
    assert pm_outbox[0]["payload"]["trust"] == "untrusted_source_evidence"
    replay_headers, replay_body = signed(revised_payload, "edit-delivery", event="issues")
    replayed = await service.accept_event(replay_headers, replay_body, secret=SECRET)
    assert replayed["status"] == "updated_evidence"
    assert len(store.list_events(initial["task_id"])) == initial_event_count + 1
    alternate_headers, alternate_body = signed(
        revised_payload, "edit-delivery-replayed-with-new-header", event="issues"
    )
    await service.accept_event(alternate_headers, alternate_body, secret=SECRET)
    assert len(store.list_events(initial["task_id"])) == initial_event_count + 1
    assert (
        len(
            [
                row
                for row in store.pending_outbox()
                if row["recipient"] == "pm-agent"
                and row["payload"]["type"] == "oss.issue.evidence_updated"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_possible_duplicate_candidates_are_uncertain_and_never_merged(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root)
    first_headers, first_body = signed(
        payload(
            31,
            "Crash saving user preferences",
            "Saving user preferences crashes after profile update.",
        )
    )
    first = await service.accept_event(first_headers, first_body, secret=SECRET)
    second_headers, second_body = signed(
        payload(
            32,
            "Crash saving user preferences",
            "Saving user preferences crashes after profile update.",
        ),
        "issue-32-delivery",
    )
    second = await service.accept_event(second_headers, second_body, secret=SECRET)
    second_task = store.get_task(second["task_id"])
    candidates = second_task["source"]["oss"]["duplicate_candidates"]
    assert candidates[0]["task_id"] == first["task_id"]
    assert candidates[0]["assessment"] == "possible_duplicate_requires_human_review"
    assert second_task["source"]["oss"]["duplicate_assessment"] == "uncertain_candidates_only"
    assert store.get_task(first["task_id"]) is not None
    assert first["task_id"] != second["task_id"]


@pytest.mark.asyncio
async def test_cancelled_issue_does_not_advance_to_engineering(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root)
    headers, body = signed(payload(44, "Fix broken behavior", "Repro steps show a failure."))
    created = await service.accept_event(headers, body, secret=SECRET)
    task = store.get_task(created["task_id"])
    store.transition_task(task["id"], "CANCELLED", task["version"])
    reconciled = await service.reconcile(task["id"])
    assert reconciled["phase"] == "cancelled"
    assert not store.connection.execute("select 1 from approval_workflows").fetchone()


@pytest.mark.asyncio
async def test_unverified_internal_envelope_cannot_bypass_webhook_signature(tmp_path):
    root = local_repo(tmp_path / "repo")
    service = maintainer(Store(tmp_path / "store.sqlite"), root)
    with pytest.raises(PermissionError, match="signature-verified"):
        await service.ingest_verified_issue(
            "owner/project",
            {"number": 5, "title": "issue", "body": ""},
            "fake-delivery",
        )


def test_operator_source_mapping_rejects_reserved_fields_and_secrets(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    with pytest.raises(ValueError, match="reserved"):
        maintainer(store, root, repo_options={"source_mapping": {"room_id": "fake"}})
    with pytest.raises(ValueError, match="secrets"):
        maintainer(store, root, repo_options={"source_mapping": {"note": "token=private"}})


@pytest.mark.asyncio
async def test_issue_content_is_sanitized_and_reproduction_is_not_claimed(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root)
    headers, body = signed(
        payload(51, "Potential leaked token", "Please inspect ghp_AAAAAAAAAAAAAAAAAAAA")
    )
    result = await service.accept_event(headers, body, secret=SECRET)
    task = store.get_task(result["task_id"])
    assert "ghp_AAAAAAAAAAAAAAAAAAAA" not in task["objective"]
    assert "REDACTED" in task["objective"]
    assert task["source"]["oss"]["reproduction_status"] == "not_run"


@pytest.mark.asyncio
async def test_research_handoff_advances_to_existing_human_gated_engineering(tmp_path):
    root = local_repo(tmp_path / "repo")
    store = Store(tmp_path / "store.sqlite")
    service = maintainer(store, root)
    headers, body = signed(payload(45, "Fix broken behavior", "Repro steps show a failure."))
    created = await service.accept_event(headers, body, secret=SECRET)
    handoff = store.get_handoff(created["research_handoff_id"])
    store.accept_handoff(handoff["id"], handoff["recipient"])
    store.complete_handoff(
        handoff["id"],
        handoff["recipient"],
        {"status": "evidence_found", "evidence": [{"source_uid": "source-1"}]},
    )
    state = await service.reconcile(created["task_id"])
    engineering = next(item for item in state["steps"].values() if item["role"] == "engineering")
    child = store.get_task(engineering["task_id"])
    workflow = ApprovalWorkflow(store, ["maintainer"]).get(child["id"])
    assert child["status"] == "AWAITING_APPROVAL"
    assert workflow["phase"] == "awaiting_engineering_approval"
    assert not store.get_handoff(engineering["task_id"])
