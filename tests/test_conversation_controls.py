"""Durable task context must not guess or cross authorization boundaries."""

from test_crew import create, source
from test_discord_interactions import setup, signed

from switchcrew.conversation import conversation_control
from switchcrew.crew import handle_crew_command
from switchcrew.discord_interactions import respond_to_interaction, slash_manifest
from switchcrew.store import Store


def test_current_task_controls_survive_reopen_and_refuse_ambiguity(tmp_path):
    path = tmp_path / "tasks.db"
    store = Store(path)
    task = create(store)
    create(store, thread="unrelated")
    store.close()
    store = Store(path)
    result = handle_crew_command(store, source("What's the status?"))
    assert result["task_id"] == task["id"]
    other = create(store)
    result = handle_crew_command(store, source("Stop the current task."))
    assert result["task_id"] is None and "Multiple tasks" in result["text"]
    assert store.get_task(task["id"])["status"] == "IN_PROGRESS"
    assert store.get_task(other["id"])["status"] == "IN_PROGRESS"
    store.close()


def test_context_cancel_uses_existing_owner_permission():
    store = Store(":memory:")
    task = create(store)
    denied = handle_crew_command(store, source("Stop the current task", user="intruder"))
    assert "not authorized" in denied["text"]
    assert store.get_task(task["id"])["status"] == "IN_PROGRESS"
    result = handle_crew_command(store, source("Stop the current task"))
    assert "cancelled" in result["text"]
    assert store.get_task(task["id"])["status"] == "CANCELLED"


def test_terminal_renders_actual_bounded_receipt_and_redacts_untrusted_output():
    store = Store(":memory:")
    task = create(store)
    handoff = store.create_handoff(task["id"], "pm", "qa", "Check", ["Pass"], task["version"])
    store.accept_handoff(handoff["id"], "qa")
    store.complete_handoff(
        handoff["id"],
        "qa",
        {
            "status": "qa_failed",
            "tested_revision": "a" * 40,
            "commands": [
                {
                    "command": ["pytest", "-q"],
                    "exit_code": 1,
                    "timed_out": False,
                    "output_summary": "FAILED regression\n@everyone ```\npassword=hush\nghp_"
                    + "a" * 40,
                }
            ],
        },
    )
    text = handle_crew_command(store, source("Show me the sandbox logs."))["text"]
    assert "FAILED regression" in text and "Exit code: 1" in text and "pytest" in text
    assert "a" * 40 in text and "recorded previews" in text
    for secret in ("@everyone", "```", "hush", "ghp_"):
        assert secret not in text
    other = handle_crew_command(store, source(f"/flip logs {task['id']}", thread="other"))
    assert "FAILED" not in other["text"]


def test_read_only_aliases_help_and_findings_do_not_create_work():
    store = Store(":memory:")
    task = create(store)
    for command in ("/flip help", "/flip tasks", f"/flip findings {task['id']}"):
        assert handle_crew_command(store, source(command))["handled"]
    assert store.get_task(task["id"])["version"] == task["version"]
    for phrase in ("merge the PR", "run rm -rf /", "approve release", "fix the first one"):
        assert conversation_control(phrase) is None


def test_signed_flip_slash_logs_and_tasks_remain_private(tmp_path):
    private, settings, store, task = setup(tmp_path)
    document = {
        "type": 2,
        "id": "interaction",
        "guild_id": "guild",
        "channel_id": "thread",
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "flip",
            "options": [{"name": "logs", "options": [{"name": "task_id", "value": task["id"]}]}],
        },
    }
    payload, headers = signed(private, document)
    reply = respond_to_interaction(store, settings, payload, headers)
    assert reply["data"]["flags"] == 64 and "No command logs" in reply["data"]["content"]
    document["data"]["options"] = [{"name": "tasks"}]
    payload, headers = signed(private, document)
    assert respond_to_interaction(store, settings, payload, headers)["data"]["flags"] == 64
    assert slash_manifest()[1]["name"] == "flip"
    store.close()


async def test_signed_merge_defers_and_uses_verified_actor_scope_and_explicit_sha(tmp_path):
    import pytest
    from fastapi import BackgroundTasks

    from switchcrew.discord_interactions import _merge_result, respond_to_interaction_async

    private, settings, store, task = setup(tmp_path)
    settings.approval_user_ids = ["alice"]
    calls = []

    class Service:
        async def approve(self, task_id, actor, version, revision):
            calls.append((task_id, actor, version, revision))
            return {"phase": "approved", "version": version + 1}

    service = Service()
    document = {
        "type": 2,
        "id": "interaction",
        "application_id": "12345",
        "token": "transient-token",
        "guild_id": "guild",
        "channel_id": "thread",
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "flip",
            "options": [
                {
                    "name": "approve-merge",
                    "options": [
                        {"name": "task_id", "value": task["id"]},
                        {"name": "revision", "value": "a" * 40},
                        {"name": "version", "value": 0},
                    ],
                }
            ],
        },
    }
    payload, headers = signed(private, document)
    background = BackgroundTasks()
    response = await respond_to_interaction_async(
        store, settings, payload, headers, merge_service=service, background_tasks=background
    )
    assert response == {"type": 5, "data": {"flags": 64}}
    assert not calls and len(background.tasks) == 1
    queued = background.tasks[0]
    # Run only the release operation, avoiding the external Discord webhook.
    await _merge_result(*queued.args[:4])
    assert calls == [(task["id"], "alice", 0, "a" * 40)]
    settings.approval_user_ids = []
    with pytest.raises(PermissionError, match="approver"):
        await respond_to_interaction_async(
            store,
            settings,
            payload,
            headers,
            merge_service=service,
            background_tasks=BackgroundTasks(),
        )
    settings.approval_user_ids = ["alice"]
    document["channel_id"] = "elsewhere"
    payload, headers = signed(private, document)
    with pytest.raises(PermissionError):
        await respond_to_interaction_async(
            store,
            settings,
            payload,
            headers,
            merge_service=service,
            background_tasks=BackgroundTasks(),
        )
    store.close()
