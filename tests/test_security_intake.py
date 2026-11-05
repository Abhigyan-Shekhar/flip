import pytest
from test_company_brain import make_repo
from test_workers import Transport, event, worker

from switchcrew.store import Store


@pytest.mark.asyncio
async def test_discord_scan_pins_operator_revision_and_replay_reuses_handoff(tmp_path):
    root, revision = make_repo(tmp_path / "repo")
    store = Store(tmp_path / "tasks.db")
    pm = worker("pm", store, Transport())
    pm.security_scans = {
        "ria-kayy/Discord-server": {
            "repo_root": str(root),
            "security_id": "security",
        }
    }
    pm.names["security"] = "security-agent"
    request = event("human", "Scan ria-kayy/Discord-server for vulnerabilities and report back.")
    await pm._request(request)
    await pm._request(request)
    rows = store.connection.execute("SELECT * FROM tasks").fetchall()
    assert len(rows) == 1
    task = store._row(rows[0])
    assert task["source"]["revision"] == revision
    assert task["source"]["repo_root"] == str(root)
    assert task["source"]["trigger_type"] == "discord_security_scan"
    assert task["source"]["user_id"] == "user"
    handoffs = store.connection.execute("SELECT * FROM handoffs").fetchall()
    assert len(handoffs) == 1
    assert store._row(handoffs[0])["recipient"] == "security"
    store.close()


@pytest.mark.asyncio
async def test_ambiguous_or_unconfigured_scan_never_executes(tmp_path):
    store = Store(tmp_path / "tasks.db")
    pm = worker("pm", store, Transport())
    pm.security_scans = {
        "org/one": {"repo_root": "/unavailable", "security_id": "security"},
        "org/two": {"repo_root": "/unavailable", "security_id": "security"},
    }
    await pm._request(event("human", "Scan this repository for vulnerabilities."))
    await pm._request(event("human", "Scan evil/unknown for vulnerabilities.", message_id="m2"))
    assert not store.connection.execute("SELECT 1 FROM handoffs").fetchone()
    assert store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
    store.close()
