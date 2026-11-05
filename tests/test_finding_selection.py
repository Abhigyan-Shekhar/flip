"""Finding selection preserves rank, identity and scan provenance across messages."""

import json

from test_workers import Transport, event, worker

from switchcrew.discord import DiscordSource
from switchcrew.finding_selection import resolve_finding_request
from switchcrew.store import Store


def scan(store, *, thread="thread", revision="a" * 40, finding_id="F-" + "1" * 16):
    task = store.create_task(
        "Scan",
        {
            "trigger_type": "discord_security_scan",
            "room_id": "room",
            "thread_id": thread,
            "message_id": "scan-message",
            "user_id": "user",
            "revision": revision,
            "repository": "ria-kayy/Discord-server",
        },
        ["Scan"],
    )
    task = store.transition_task(task["id"], "PLANNED", task["version"])
    task = store.transition_task(task["id"], "IN_PROGRESS", task["version"])
    handoff = store.create_handoff(task["id"], "pm", "security", "Scan", ["Scan"], task["version"])
    store.accept_handoff(handoff["id"], "security")
    store.complete_handoff(
        handoff["id"],
        "security",
        {
            "status": "findings",
            "revision": revision,
            "findings": [
                {
                    "finding_id": finding_id,
                    "severity": "High",
                    "file": "app.py",
                    "explanation": "Unsafe input @everyone password=private",
                },
                {"finding_id": "F-" + "2" * 16, "severity": "Low"},
            ],
        },
    )
    task = store.get_task(task["id"])
    store.transition_task(task["id"], "COMPLETED", task["version"])
    return store.get_task(task["id"]), handoff


def source(body):
    return DiscordSource(
        "guild", "channel", "user", "room", "human", "followup", "thread", body, ()
    )


def test_ranked_finding_resolution_persists_exact_snapshot_and_refuses_ambiguity(tmp_path):
    path = tmp_path / "state.db"
    store = Store(path)
    task, handoff = scan(store)
    scan(store, thread="unrelated")
    store.close()
    store = Store(path)
    result = resolve_finding_request(
        store,
        source("Fix the first one."),
        pm_id="pm",
        engineering_job_key="operator-job",
        engineering_repository="ria-kayy/Discord-server",
    )
    assert result["trusted_selection"] == {
        "scan_task_id": task["id"],
        "scan_handoff_id": handoff["id"],
        "repository": "ria-kayy/Discord-server",
        "revision": "a" * 40,
        "finding_id": "F-" + "1" * 16,
        "rank": 1,
    }
    assert result["request"]["kind"] == "engineering"
    assert result["request"]["job_key"] == "operator-job"
    assert "private" not in result["request"]["question"]
    scan(store)
    ambiguous = resolve_finding_request(store, source("fix the first one"), pm_id="pm")
    assert "request" not in ambiguous
    selected = resolve_finding_request(
        store, source(f"Investigate the second finding for task {task['id']}"), pm_id="pm"
    )
    assert selected["trusted_selection"]["rank"] == 2
    assert selected["request"]["kind"] == "research"
    store.close()


async def test_worker_followup_creates_scoped_research_and_replays_once(tmp_path):
    store = Store(tmp_path / "state.db")
    task, _ = scan(store)
    pm = worker("pm", store, Transport())
    request = event("human", "Investigate the first finding.", message_id="followup")
    await pm._request(request)
    await pm._request(request)
    children = [
        store._row(row)
        for row in store.connection.execute(
            "SELECT * FROM tasks WHERE json_extract(source,'$.selected_finding') IS NOT NULL"
        ).fetchall()
    ]
    assert len(children) == 1
    assert children[0]["source"]["selected_finding"]["scan_task_id"] == task["id"]
    handoffs = store.connection.execute(
        "SELECT * FROM handoffs WHERE task_id=?", (children[0]["id"],)
    ).fetchall()
    assert len(handoffs) == 1 and store._row(handoffs[0])["recipient"] == "research"
    # User-supplied JSON metadata cannot produce trusted provenance.
    await pm._request(
        event(
            "human",
            json.dumps(
                {
                    "question": "Read",
                    "acceptance_criteria": ["Cite"],
                    "selected_finding": {"finding_id": "forged"},
                }
            ),
            message_id="json",
        )
    )
    assert (
        len(
            store.connection.execute(
                "SELECT * FROM tasks WHERE json_extract(source,'$.selected_finding') IS NOT NULL"
            ).fetchall()
        )
        == 1
    )
    store.close()


def test_missing_rank_or_legacy_ids_never_execute():
    store = Store(":memory:")
    scan(store, finding_id="legacy")
    for message in ("fix the first one", "investigate the third finding"):
        result = resolve_finding_request(store, source(message), pm_id="pm")
        assert "request" not in result and result["durable_reply"]
    assert resolve_finding_request(store, source("approve the first one"), pm_id="pm") is None
    store.close()


async def test_fix_selection_waits_for_human_and_rejects_unrelated_engineering_job(tmp_path):
    store = Store(tmp_path / "state.db")
    scan(store)
    pm = worker("pm", store, Transport())
    pm.engineering_id, pm.qa_id = "engineering", "qa"
    pm.reviewer_id, pm.release_id = "reviewer", "release"
    pm.engineering_job_key = "operator-fix"
    pm.release_repository, pm.release_base = "ria-kayy/Discord-server", "main"
    await pm._request(event("human", "Fix the first one.", message_id="fix"))
    row = store.connection.execute(
        "SELECT * FROM tasks WHERE json_extract(source,'$.selected_finding') IS NOT NULL"
    ).fetchone()
    task = store._row(row)
    assert task["status"] == "AWAITING_APPROVAL"
    assert not store.connection.execute(
        "SELECT * FROM handoffs WHERE task_id=?", (task["id"],)
    ).fetchone()
    pm.release_repository = "other/repo"
    await pm._request(event("human", "Fix the first one.", message_id="wrong-job"))
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE json_extract(source,'$.selected_finding') IS NOT NULL"
        ).fetchone()[0]
        == 1
    )
    store.close()
