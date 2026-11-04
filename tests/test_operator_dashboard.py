from fastapi.testclient import TestClient

from switchcrew.api import create_app
from switchcrew.operator_dashboard import snapshot
from switchcrew.settings import Settings
from switchcrew.store import Store


def test_dashboard_snapshot_uses_only_bounded_safe_fields(tmp_path):
    store = Store(tmp_path / "dashboard.sqlite3")
    task = store.create_task(
        "Investigate an incident",
        {"private_prompt": "never-leak", "channel_id": "channel"},
        ["Find evidence"],
    )
    report = snapshot(store, Settings(_env_file=None, pm_agent_id="pm-agent"))
    assert report["tasks"][0]["id"] == task["id"]
    assert report["agents"] == [{"role": "pm", "id": "pm-agent", "live": "not_probed"}]
    assert "never-leak" not in str(report)
    assert report["summary"]["listed_tasks"] == 1
    assert report["activity"]
    store.close()


def test_dashboard_requires_token_for_data_and_exposes_no_raw_secrets(tmp_path):
    token = "sensitive" * 5
    settings = Settings(_env_file=None, api_token=token, database_path=tmp_path / "api.db")
    with TestClient(create_app(settings)) as client:
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert "Flip" in page.text
        assert token not in page.text
        assert page.headers["Cache-Control"] == "no-store"
        assert client.get("/operator/snapshot").status_code == 401
        result = client.get("/operator/snapshot", headers={"Authorization": f"Bearer {token}"})
        assert result.status_code == 200
        assert result.json()["summary"]["listed_tasks"] == 0
        assert token not in result.text
        assert (
            client.get(
                "/operator/snapshot?limit=999", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 422
        )
