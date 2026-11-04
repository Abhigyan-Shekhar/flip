import json
import os

import pytest

from switchcrew.employees import EmployeeAuthorizationError, EmployeeDirectory
from switchcrew.store import ConflictError, Store


def registration(tmp_path, employee_id="ria", actor="discord-ria", switch="switch-ria"):
    base = tmp_path / employee_id
    workspace = base / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    control = base / "console-control.json"
    control.write_text(json.dumps({"port": 4200, "token": "private-test-token"}))
    os.chmod(control, 0o600)
    database = base / "console.sqlite3"
    database.write_text("employee-local-console-db")
    os.chmod(database, 0o600)
    return {
        "employee_id": employee_id,
        "actor_id": actor,
        "switch_agent_id": switch,
        "codex_switch_agent_id": f"console-provider-{employee_id}",
        "workspace_root": str(workspace),
        "allowed_roots": [str(workspace)],
        "control_file": str(control),
        "console_agent_id": f"console-{employee_id}",
        "codex_name": f"Codex {employee_id}",
        "console_database": str(database),
    }


def active_task(store, task_id="task-1"):
    task = store.create_task("Do approved work", {}, ["Provide evidence"], task_id)
    task = store.transition_task(task_id, "PLANNED", task["version"])
    task = store.transition_task(task_id, "IN_PROGRESS", task["version"])
    return task


def test_explicit_employee_assignment_is_task_scoped_and_uses_verified_handoff(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    config = registration(tmp_path)
    directory = EmployeeDirectory(store, [config], allowed_assigners={"trusted-pm"})
    task = active_task(store)
    directory.assign_task(
        task["id"], "ria", assigned_by="trusted-pm", expected_version=task["version"]
    )
    handoff = store.create_handoff(
        task["id"], "trusted-pm", "switch-ria", "Implement this", [], task["version"]
    )

    access = directory.authorize_assignment(
        "ria", task["id"], actor_id="discord-ria", handoff_id=handoff["id"]
    )
    assert access["switch_agent_id"] == "switch-ria"
    assert access["workspace_root"] == config["workspace_root"]
    assert access["session_key"]
    assert "token" not in access
    assert directory.assigned_employee(task["id"]) == "ria"
    assert store.get_task(task["id"])["source"] == {}

    replay = store.create_task("Do approved work", {}, ["Provide evidence"], task["id"])
    assert replay["source"] == {}
    store.close()


def test_wrong_actor_unassigned_task_and_cross_workspace_are_denied(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    config = registration(tmp_path)
    directory = EmployeeDirectory(store, [config], allowed_assigners={"trusted-pm"})
    task = active_task(store)
    handoff = store.create_handoff(
        task["id"], "trusted-pm", "switch-ria", "Implement this", [], task["version"]
    )
    with pytest.raises(EmployeeAuthorizationError):
        directory.authorize_assignment(
            "ria", task["id"], actor_id="discord-other", handoff_id=handoff["id"]
        )
    directory.assign_task(task["id"], "ria", assigned_by="trusted-pm")
    with pytest.raises(EmployeeAuthorizationError):
        directory.authorize_assignment(
            "ria", task["id"], actor_id="discord-ria", handoff_id="other-handoff"
        )
    with pytest.raises(EmployeeAuthorizationError):
        directory.authorize_assignment(
            "ria", task["id"], tmp_path, actor_id="discord-ria", handoff_id=handoff["id"]
        )
    store.close()


def test_untrusted_assigner_and_revoked_assignment_cannot_authorize_session(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    directory = EmployeeDirectory(store, [registration(tmp_path)], allowed_assigners={"trusted-pm"})
    task = active_task(store)
    with pytest.raises(EmployeeAuthorizationError):
        directory.assign_task(task["id"], "ria", assigned_by="discord-ria")
    directory.assign_task(task["id"], "ria", assigned_by="trusted-pm")
    handoff = store.create_handoff(
        task["id"], "trusted-pm", "switch-ria", "Implement this", [], task["version"]
    )
    directory.authorize_assignment(
        "ria", task["id"], actor_id="discord-ria", handoff_id=handoff["id"]
    )
    directory.revoke_assignment(task["id"], "ria", revoked_by="trusted-pm")
    with pytest.raises(EmployeeAuthorizationError):
        directory.authorize_assignment(
            "ria", task["id"], actor_id="discord-ria", handoff_id=handoff["id"]
        )
    store.close()


def test_session_status_survives_restart_and_session_identity_is_immutable(tmp_path):
    store_path = tmp_path / "app.sqlite3"
    config = registration(tmp_path)
    store = Store(store_path)
    directory = EmployeeDirectory(store, [config], allowed_assigners={"trusted-pm"})
    task = active_task(store)
    directory.assign_task(task["id"], "ria", assigned_by="trusted-pm")
    handoff = store.create_handoff(
        task["id"], "trusted-pm", "switch-ria", "Implement this", [], task["version"]
    )
    authorized = directory.authorize_assignment(
        "ria", task["id"], actor_id="discord-ria", handoff_id=handoff["id"]
    )
    directory.record_session(
        task["id"], "ria", handoff["id"], "console-session-123", "disconnected"
    )
    store.close()

    reopened = Store(store_path)
    directory2 = EmployeeDirectory(reopened, [config], allowed_assigners={"trusted-pm"})
    status = directory2.session_status(task["id"], "ria", handoff["id"])
    assert status["status"] == "disconnected"
    assert status["session_key"] == authorized["session_key"]
    with pytest.raises(ConflictError):
        directory2.record_session(
            task["id"], "ria", handoff["id"], "different-session", "recovering"
        )
    reopened.close()


def test_changed_identity_duplicates_and_overlapping_employee_workspaces_reject(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    original = registration(tmp_path)
    EmployeeDirectory(store, [original], allowed_assigners={"trusted-pm"})
    changed = {**original, "actor_id": "somebody-else"}
    with pytest.raises(ConflictError):
        EmployeeDirectory(store, [changed], allowed_assigners={"trusted-pm"})
    with pytest.raises(ValueError):
        EmployeeDirectory(
            store,
            [original, registration(tmp_path, "other", "discord-ria", "switch-other")],
            allowed_assigners={"trusted-pm"},
        )
    duplicate_console = registration(tmp_path, "console-dupe", "discord-dupe", "switch-dupe")
    duplicate_console["console_agent_id"] = original["console_agent_id"]
    with pytest.raises(ValueError):
        EmployeeDirectory(
            store,
            [original, duplicate_console],
            allowed_assigners={"trusted-pm"},
        )
    with pytest.raises(ValueError):
        EmployeeDirectory(
            store,
            [
                original,
                registration(
                    tmp_path / "ria" / "workspace", "other", "discord-other", "switch-other"
                ),
            ],
            allowed_assigners={"trusted-pm"},
        )
    store.close()


def test_private_registration_file_and_disabled_employee(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    config = registration(tmp_path)
    path = tmp_path / "employees.json"
    path.write_text(json.dumps({"employees": [{**config, "enabled": False}]}))
    os.chmod(path, 0o600)
    directory = EmployeeDirectory.from_file(store, path, allowed_assigners={"trusted-pm"})
    with pytest.raises(EmployeeAuthorizationError):
        directory.assign_task("task-absent", "ria", assigned_by="trusted-pm")
    store.close()
