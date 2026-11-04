import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from switchcrew import engineering_qa
from switchcrew.approval import ApprovalWorkflow
from switchcrew.employees import EmployeeDirectory
from switchcrew.engineering import Ledger, assignment_id
from switchcrew.store import Store


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def repo(path):
    path.mkdir(parents=True)
    (path / "README.md").write_text("employee workspace\n")
    git(path, "init")
    git(path, "add", "README.md")
    git(
        path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return git(path, "rev-parse", "HEAD")


def employee_config(tmp_path, workspace):
    private = tmp_path / "private"
    private.mkdir(exist_ok=True)
    control = private / "ria-console.json"
    control.write_text(json.dumps({"port": 4400, "token": "never-return-this-token"}))
    os.chmod(control, 0o600)
    console_db = private / "ria-console.sqlite3"
    console_db.write_text("fixture db")
    os.chmod(console_db, 0o600)
    registration = {
        "employee_id": "ria",
        "actor_id": "discord-user-ria",
        "switch_agent_id": "switch-ria",
        "codex_switch_agent_id": "switch-provider-codex-ria",
        "workspace_root": str(workspace),
        "allowed_roots": [str(workspace)],
        "control_file": str(control),
        "console_agent_id": "console-ria",
        "codex_name": "Ria Codex",
        "console_database": str(console_db),
    }
    registrations_path = private / "employees.json"
    registrations_path.write_text(json.dumps({"employees": [registration]}))
    os.chmod(registrations_path, 0o600)
    return registration, {
        "registrations_file": str(registrations_path),
        "allowed_assigners": ["pm-agent"],
        "employee_id": "ria",
        "actor_id": "discord-user-ria",
    }


def approved_task(store, approver="reviewer-human"):
    task = store.create_task(
        "Implement employee task",
        {"requester": "discord-user-ria"},
        ["Commit the requested change"],
        "employee-task",
    )
    approvals = ApprovalWorkflow(store, approvers={approver})
    approvals.request(
        task["id"],
        "pm-agent",
        "switch-ria",
        "qa-agent",
        "release-agent",
        "build",
        "org/repo",
        "main",
        reviewer="reviewer-agent",
    )
    pending = store.get_task(task["id"])
    approvals.decide(task["id"], "engineering", True, approver, pending["version"])
    managed = approvals.get(task["id"])
    return approvals, managed["engineering_handoff"]


class FakeConsole:
    instances = []
    revoke = None

    def __init__(
        self,
        control_file,
        console_agent_id,
        codex_name,
        switch,
        *,
        console_database,
        switch_agent_id,
    ):
        self.args = {
            "control_file": control_file,
            "console_agent_id": console_agent_id,
            "codex_name": codex_name,
            "console_database": console_database,
            "switch_agent_id": switch_agent_id,
        }
        self.calls = []
        self.__class__.instances.append(self)

    async def validate(self, checkout):
        self.calls.append(("validate", checkout))
        if self.__class__.revoke:
            self.__class__.revoke()

    async def find(self, identity):
        self.calls.append(("find", identity))
        return None

    async def create(self, identity):
        self.calls.append(("create", identity))
        return "real-console-session-fixture"

    async def deliver(self, identity, checkout, question, criteria):
        self.calls.append(("deliver", identity))
        return "switch-event-fixture"


class FakeEngineeringExecutor:
    instances = []

    def __init__(self, ledger, root, source, base_revision, session, *, authorize=None, **kwargs):
        self.ledger, self.root, self.source = ledger, root, source
        self.session, self.authorize = session, authorize
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    async def __call__(self, state):
        identity = assignment_id(state["handoff_id"])
        self.authorize(state)
        try:
            await self.session.validate(self.source)
            session_id = await self.session.find(identity)
            if session_id is None:
                session_id = await self.session.create(identity)
            await self.session.deliver(
                identity, self.source, state["question"], state["acceptance_criteria"]
            )
        except PermissionError:
            return {"status": "pending_execution", "assignment_id": identity}
        except OSError:
            return {"status": "pending_execution", "assignment_id": identity}
        return {"status": "implemented", "assignment_id": identity, "session_id": session_id}


@pytest.fixture
def runtime_case(tmp_path, monkeypatch):
    FakeConsole.instances.clear()
    FakeConsole.revoke = None
    FakeEngineeringExecutor.instances.clear()
    workspace = tmp_path / "employee-workspace"
    workspace.mkdir()
    source = workspace / "source"
    base = repo(source)
    registration, employee = employee_config(tmp_path, workspace)
    store = Store(tmp_path / "app.sqlite3")
    approvals, handoff_id = approved_task(store)
    task = store.get_task("employee-task")
    directory = EmployeeDirectory(store, [registration], allowed_assigners={"pm-agent"})
    directory.assign_task(task["id"], "ria", assigned_by="pm-agent")
    handoff = store.get_handoff(handoff_id)
    config = {
        "employee": employee,
        "job_templates": {
            "build": {
                "source": str(source),
                "base_revision": base,
                "codex_switch_agent_id": "switch-provider-codex-ria",
            }
        },
        "checkout_root": str(workspace / "checkouts"),
    }
    state = {
        "task_id": task["id"],
        "handoff_id": handoff_id,
        "question": handoff["question"],
        "acceptance_criteria": handoff["acceptance_criteria"],
    }
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(engineering_qa, "ConsoleCodex", FakeConsole)
    monkeypatch.setattr(engineering_qa, "EngineeringExecutor", FakeEngineeringExecutor)
    connection = SimpleNamespace(role="engineering", agent_id="switch-ria")
    execute = engineering_qa.configured_executor(
        config, connection, object(), store, ledger, approvals
    )
    yield SimpleNamespace(
        store=store,
        directory=directory,
        registration=registration,
        employee=employee,
        source=source,
        workspace=workspace,
        task=task,
        handoff=handoff,
        state=state,
        ledger=ledger,
        execute=execute,
        config=config,
        approvals=approvals,
    )
    ledger.close()
    store.close()


@pytest.mark.asyncio
async def test_employee_runtime_uses_private_profile_and_records_actual_session(runtime_case):
    result = await runtime_case.execute(runtime_case.state)

    assert result["status"] == "implemented"
    console = FakeConsole.instances[0]
    assert console.args["control_file"] == runtime_case.registration["control_file"]
    assert console.args["console_agent_id"] == "console-ria"
    assert console.args["codex_name"] == "Ria Codex"
    assert console.args["switch_agent_id"] == "switch-provider-codex-ria"
    assert "token" not in console.args
    status = runtime_case.directory.session_status(
        runtime_case.task["id"], "ria", runtime_case.handoff["id"]
    )
    assert status["console_session_id"] == "real-console-session-fixture"
    assert status["status"] == "completed"
    assert len(console.calls) == 4


@pytest.mark.asyncio
async def test_unassigned_task_and_wrong_provider_switch_id_never_construct_console(runtime_case):
    runtime_case.directory.revoke_assignment(runtime_case.task["id"], "ria", revoked_by="pm-agent")
    result = await runtime_case.execute(runtime_case.state)
    assert result["status"] == "blocked"
    assert FakeConsole.instances == []


@pytest.mark.asyncio
async def test_outside_employee_workspace_is_blocked_before_console_action(runtime_case):
    runtime_case.config["checkout_root"] = str(runtime_case.workspace.parent / "other")
    result = await runtime_case.execute(runtime_case.state)
    assert result["status"] == "blocked_configuration"
    assert FakeConsole.instances == []


@pytest.mark.asyncio
async def test_assignment_revocation_during_console_validation_blocks_followup_calls(runtime_case):
    FakeConsole.revoke = lambda: runtime_case.directory.revoke_assignment(
        runtime_case.task["id"], "ria", revoked_by="pm-agent"
    )
    result = await runtime_case.execute(runtime_case.state)
    assert result["status"] == "pending_execution"
    assert [method for method, _ in FakeConsole.instances[0].calls] == ["validate"]
    status = runtime_case.directory.session_status(
        runtime_case.task["id"], "ria", runtime_case.handoff["id"]
    )
    assert status["status"] == "blocked"


@pytest.mark.asyncio
async def test_codex_switch_registration_mismatch_is_not_overridden(runtime_case):
    runtime_case.config["job_templates"]["build"]["codex_switch_agent_id"] = "made-up-id"
    result = await runtime_case.execute(runtime_case.state)
    assert result["status"] == "blocked_configuration"
    assert FakeConsole.instances == []
