from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from switchcrew.api import create_app
from switchcrew.settings import Settings

TOKEN = "test-credential-with-at-least-32-characters"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "tasks.sqlite3", api_token=TOKEN)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value


def task_body(**changes):
    return {"objective": "Research Switch", "acceptance_criteria": ["Cite sources"], **changes}


def test_health_auth_validation_and_secret_errors(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/tasks", json=task_body()).status_code == 401
    assert client.get("/openapi.json").status_code == 401
    for body in (
        task_body(objective=" "),
        task_body(acceptance_criteria=[" "]),
        task_body(password=TOKEN),
        task_body(objective={"token": TOKEN}),
    ):
        response = client.post("/tasks", headers=HEADERS, json=body)
        assert response.status_code == 422
        assert TOKEN not in response.text
    assert client.get("/tasks/missing", headers=HEADERS).status_code == 404
    assert client.get("/tasks/missing/events", headers=HEADERS).status_code == 404


def test_task_persistence_versions_and_errors(client, settings):
    response = client.post("/tasks", headers=HEADERS, json=task_body())
    assert response.status_code == 201
    task = response.json()
    url = f"/tasks/{task['id']}"
    transition = {"status": "NEEDS_CLARIFICATION", "expected_version": task["version"]}
    assert client.post(f"{url}/transitions", json=transition, headers=HEADERS).status_code == 200
    assert client.post(f"{url}/transitions", json=transition, headers=HEADERS).status_code == 409
    assert (
        client.post(
            f"{url}/transitions",
            json={"status": "COMPLETED", "expected_version": 1},
            headers=HEADERS,
        ).status_code
        == 409
    )
    assert len(client.get(f"{url}/events", headers=HEADERS).json()) == 2
    with TestClient(create_app(settings)) as reopened:
        assert reopened.get(url, headers=HEADERS).json()["version"] == 1


def test_parallel_task_writes(client):
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(
            pool.map(
                lambda i: client.post(
                    "/tasks", headers=HEADERS, json=task_body(objective=f"Task {i}")
                ),
                range(18),
            )
        )
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 18


def test_configured_secrets_redacted_from_responses(client):
    response = client.post("/tasks", headers=HEADERS, json=task_body(objective=f"Review {TOKEN}"))
    assert response.status_code == 201
    assert TOKEN not in response.text
    assert "[REDACTED]" in response.text
    assert TOKEN not in client.get(f"/tasks/{response.json()['id']}", headers=HEADERS).text


def test_safe_unexpected_error(settings, monkeypatch, capsys):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:

        def fail(*args, **kwargs):
            raise RuntimeError(TOKEN)

        monkeypatch.setattr(client.app.state.store, "create_task", fail)
        response = client.post("/tasks", headers=HEADERS, json=task_body())
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
    assert TOKEN not in capsys.readouterr().err


def test_settings_fail_closed(tmp_path):
    with pytest.raises(RuntimeError, match="SWITCHCREW_API_TOKEN"):
        create_app(Settings(_env_file=None))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_token=SecretStr("short"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, switch_base_url="http://example.com")


@pytest.mark.parametrize(
    "status",
    ["AWAITING_APPROVAL", "QA_REVIEW", "PASSED", "HUMAN_REVIEW", "PUBLISH_APPROVED", "PR_OPEN"],
)
def test_deferred_states_are_not_exposed(client, status):
    task = client.post("/tasks", headers=HEADERS, json=task_body()).json()
    response = client.post(
        f"/tasks/{task['id']}/transitions",
        headers=HEADERS,
        json={"status": status, "expected_version": task["version"]},
    )
    assert response.status_code == 422
    current = client.get(f"/tasks/{task['id']}", headers=HEADERS).json()
    assert current["status"] == "DRAFT"
    assert current["version"] == 0
    assert len(client.get(f"/tasks/{task['id']}/events", headers=HEADERS).json()) == 1
    schema = client.get("/openapi.json", headers=HEADERS).json()
    assert status not in schema["components"]["schemas"]["DayOneStatus"]["enum"]


def test_dynamic_validation_keys_cannot_echo_configured_credentials(client):
    response = client.post("/tasks", headers=HEADERS, json=task_body(**{TOKEN: "secret"}))
    assert response.status_code == 422
    assert TOKEN not in response.text


def test_model_generated_plan_step_requires_human_review_api(tmp_path):
    reviewer_token = "independent-human-review-credential-123456"
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "plan-review.sqlite3",
        api_token=TOKEN,
        approval_credentials={"human-reviewer": SecretStr(reviewer_token)},
        pm_agent_id="pm-agent",
        research_agent_id="research-agent",
    )
    with TestClient(create_app(settings)) as client:
        created = client.post("/tasks", headers=HEADERS, json=task_body(kind="orchestrate"))
        assert created.status_code == 201
        parent = created.json()
        plan_url = f"/tasks/{parent['id']}/plan"
        step = client.get(plan_url, headers=HEADERS).json()["steps"]["step-1"]
        store = client.app.state.store
        store.accept_handoff(step["handoff_id"], "research-agent")
        store.complete_handoff(
            step["handoff_id"],
            "research-agent",
            {"status": "evidence_found", "trust": "model_generated_requires_human_review"},
        )
        plan = client.get(plan_url, headers=HEADERS).json()
        assert plan["steps"]["step-1"]["status"] == "awaiting_review"
        decision = {
            "step_id": "step-1",
            "approved": True,
            "expected_version": client.get(f"/tasks/{parent['id']}", headers=HEADERS).json()[
                "version"
            ],
        }
        assert client.post(f"{plan_url}/reviews", headers=HEADERS, json=decision).status_code == 403
        response = client.post(
            f"{plan_url}/reviews",
            headers={"Authorization": f"Bearer {reviewer_token}"},
            json=decision,
        )
        assert response.status_code == 200
        assert response.json()["phase"] == "completed"
        assert client.get(f"/tasks/{parent['id']}", headers=HEADERS).json()["status"] == "COMPLETED"


def test_waggle_credential_is_redacted_from_api_output(tmp_path):
    key = "local-memory-credential-for-redaction"
    cfg = Settings(
        _env_file=None,
        database_path=tmp_path / "memory-redaction.sqlite3",
        api_token=TOKEN,
        waggle_token=key,
    )
    with TestClient(create_app(cfg)) as client:
        response = client.post("/tasks", headers=HEADERS, json=task_body(objective=f"Review {key}"))
        assert response.status_code == 201
        assert key not in response.text
        assert "[REDACTED]" in response.text
