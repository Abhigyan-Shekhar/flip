import json

from fastapi.testclient import TestClient
from test_employees import registration

from switchcrew.api import create_app
from switchcrew.settings import Settings


def test_assignment_reservation_precedes_approval_and_invalidates_old_version(tmp_path):
    profile = registration(tmp_path)
    registry = tmp_path / "employees.json"
    registry.write_text(json.dumps({"employees": [profile]}))
    registry.chmod(0o600)
    organization = tmp_path / "organization.json"
    organization.write_text(
        json.dumps(
            {"employees": {"registrations_file": str(registry), "allowed_assigners": ["manager"]}}
        )
    )
    organization.chmod(0o600)
    config = Settings(
        _env_file=None,
        database_path=tmp_path / "app.db",
        api_token="x" * 40,
        organization_config=organization,
        approval_credentials={"manager": "m" * 40},
        human_credentials={"discord-ria": "r" * 40},
        pm_agent_id="pm",
        engineering_agent_id="engineering",
        qa_agent_id="qa",
        reviewer_agent_id="reviewer",
        release_agent_id="release",
        release_repository="owner/project",
        release_base="main",
    )
    with TestClient(create_app(config)) as client:
        task = client.post(
            "/tasks",
            headers={"Authorization": "Bearer " + "x" * 40},
            json={
                "kind": "engineering",
                "job_key": "build",
                "objective": "Fix boundary",
                "acceptance_criteria": ["Regression passes"],
            },
        ).json()
        original_source = dict(task["source"])
        path = f"/tasks/{task['id']}/employee"
        body = {"employee_id": "ria", "expected_version": task["version"]}
        assert (
            client.post(
                path, headers={"Authorization": "Bearer " + "r" * 40}, json=body
            ).status_code
            == 403
        )
        manager = {"Authorization": "Bearer " + "m" * 40}
        assigned = client.post(path, headers=manager, json=body)
        assert assigned.status_code == 200
        assert assigned.json()["version"] == task["version"] + 1
        assert "control_file" not in assigned.text and profile["control_file"] not in assigned.text
        store = client.app.state.store
        assert store.get_task(task["id"])["source"] == original_source
        assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
        assert client.app.state.approvals.get(task["id"])["spec"]["engineering"] == "switch-ria"
        approval = {"stage": "engineering", "approved": True, "expected_version": task["version"]}
        assert (
            client.post(
                f"/tasks/{task['id']}/approvals", headers=manager, json=approval
            ).status_code
            == 409
        )
        approval["expected_version"] = assigned.json()["version"]
        decision = client.post(f"/tasks/{task['id']}/approvals", headers=manager, json=approval)
        assert decision.status_code == 200
        assert (
            store.get_handoff(decision.json()["engineering_handoff"])["recipient"] == "switch-ria"
        )
        assert (
            client.post(
                path,
                headers=manager,
                json={**body, "expected_version": store.get_task(task["id"])["version"]},
            ).status_code
            == 409
        )
