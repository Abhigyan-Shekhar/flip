import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from switchcrew.store import Store
from switchcrew.watchdog import Watchdog, record_worker_heartbeat


def make_task(store, task_id="task-1"):
    task = store.create_task("Task", {}, ["Done"], task_id)
    task = store.transition_task(task_id, "PLANNED", task["version"])
    return store.transition_task(task_id, "IN_PROGRESS", task["version"])


def make_handoff(store, task_id="task-1", recipient="agent-a"):
    task = store.get_task(task_id)
    return store.create_handoff(task_id, "pm-agent", recipient, "Question", [], task["version"])


def config(**overrides):
    return {
        "watchdog_id": "watchdog",
        "monitored_agents": ["agent-a"],
        "alert_sender": "pm-agent",
        "alert_after_seconds": 30,
        "handoff_timeout_seconds": 3600,
        "outbox_timeout_seconds": 3600,
        "execution_timeout_seconds": 30,
        **overrides,
    }


def watchdog_alert_count(store):
    return sum(
        json.loads(row[0]).get("type") == "watchdog.alert"
        for row in store.connection.execute("SELECT payload FROM outbox").fetchall()
    )


def test_missing_heartbeat_alert_is_deduplicated_and_worker_recovery_resolves(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    task = make_task(store)
    make_handoff(store, recipient="agent-a")
    store.connection.execute("UPDATE outbox SET status='sent'")
    watchdog = Watchdog(store, config=config())
    start = datetime.now(UTC)
    assert watchdog.poll_once(now=start)["findings"] == []
    first = watchdog.poll_once(now=start + timedelta(seconds=31))
    assert len(first["findings"]) == 1
    second = watchdog.poll_once(now=start + timedelta(seconds=32))
    assert len(second["findings"]) == 1
    assert watchdog_alert_count(store) == 1
    watchdog.record_heartbeat("agent-a", at=start + timedelta(seconds=33))
    watchdog.poll_once(now=start + timedelta(seconds=34))
    finding = store.connection.execute(
        "SELECT status,recovery_state FROM watchdog_findings WHERE kind='worker_heartbeat'"
    ).fetchone()
    assert tuple(finding) == ("resolved", "heartbeat_recovered")
    assert store.get_task(task["id"])["status"] == "IN_PROGRESS"
    store.close()


def test_open_watchdog_finding_and_dedupe_survive_database_restart(tmp_path):
    database = tmp_path / "app.sqlite3"
    store = Store(database)
    make_task(store)
    make_handoff(store, recipient="agent-a")
    store.connection.execute("UPDATE outbox SET status='sent'")
    start = datetime.now(UTC)
    watchdog = Watchdog(store, config=config())
    watchdog.poll_once(now=start)
    result = watchdog.poll_once(now=start + timedelta(seconds=31))
    fingerprint = result["findings"][0]
    store.close()

    reopened = Store(database)
    second = Watchdog(reopened, config=config())
    result = second.poll_once(now=start + timedelta(seconds=32))
    assert result["findings"] == [fingerprint]
    assert watchdog_alert_count(reopened) == 1
    assert (
        reopened.connection.execute(
            "SELECT status FROM watchdog_findings WHERE fingerprint=?", (fingerprint,)
        ).fetchone()[0]
        == "open"
    )
    reopened.close()


def test_stale_handoff_alerts_once_then_task_cancel_resolves_without_retry(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    task = make_task(store)
    handoff = make_handoff(store)
    watchdog = Watchdog(
        store,
        config=config(handoff_timeout_seconds=1, alert_after_seconds=3600),
    )
    current = datetime.now(UTC) + timedelta(seconds=5)
    first = watchdog.poll_once(now=current)
    second = watchdog.poll_once(now=current + timedelta(seconds=1))
    assert first["findings"] == second["findings"]
    assert watchdog_alert_count(store) == 1
    latest = store.get_task(task["id"])
    cancelled = store.transition_task(task["id"], "CANCELLED", latest["version"])
    watchdog.poll_once(now=current + timedelta(seconds=2))
    finding = store.connection.execute(
        "SELECT status,recovery_state FROM watchdog_findings WHERE kind='handoff_timeout'"
    ).fetchone()
    assert tuple(finding) == ("resolved", "handoff_terminal_or_missing")
    assert store.get_handoff(handoff["id"])["status"] == "pending"
    assert cancelled["status"] == "CANCELLED"
    store.close()


def test_pending_outbox_age_is_observed_durably_without_replaying_delivery(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    task = make_task(store)
    store.connection.execute("UPDATE outbox SET status='sent'")
    store.enqueue_outbox(task["id"], "discord", {"text": "alert"}, "test-key")
    watchdog = Watchdog(store, config=config(outbox_timeout_seconds=1, alert_after_seconds=3600))
    current = datetime.now(UTC)
    watchdog.poll_once(now=current)
    result = watchdog.poll_once(now=current + timedelta(seconds=2))
    assert len(result["findings"]) == 1
    assert watchdog_alert_count(store) == 1
    store.close()


def test_watchdog_does_not_recursively_alert_on_its_own_notification(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    task = make_task(store)
    make_handoff(store, recipient="agent-a")
    store.connection.execute("UPDATE outbox SET status='sent'")
    watchdog = Watchdog(
        store,
        config=config(
            alert_after_seconds=1,
            outbox_timeout_seconds=1,
            handoff_timeout_seconds=3600,
        ),
    )
    start = datetime.now(UTC)
    watchdog.poll_once(now=start)
    watchdog.poll_once(now=start + timedelta(seconds=2))
    watchdog.poll_once(now=start + timedelta(seconds=5))
    assert watchdog_alert_count(store) == 1
    assert store.get_task(task["id"])["status"] == "IN_PROGRESS"
    store.close()


def test_uncertain_console_execution_is_operator_only_and_not_retried(tmp_path):
    ledger = tmp_path / "console-ledger.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE executions(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
        db.execute(
            "INSERT INTO executions VALUES(?,?)",
            ("assignment", json.dumps({"phase": "delivering"})),
        )
    store = Store(tmp_path / "app.sqlite3")
    task = make_task(store)
    watchdog = Watchdog(
        store,
        config=config(
            execution_ledger=str(ledger),
            execution_timeout_seconds=1,
            alert_after_seconds=3600,
        ),
    )
    current = datetime.now(UTC)
    watchdog.poll_once(now=current)
    result = watchdog.poll_once(now=current + timedelta(seconds=2))
    finding = store.connection.execute(
        "SELECT severity,details FROM watchdog_findings WHERE kind='execution_uncertain'"
    ).fetchone()
    assert result["execution_ledger"] == "available"
    assert finding["severity"] == "High"
    assert json.loads(finding["details"])["action"] == "operator_reconciliation"
    assert store.get_task(task["id"])["status"] == "IN_PROGRESS"
    assert watchdog_alert_count(store) == 0
    store.close()


def test_missing_watchdog_configuration_and_required_ledger_are_explicit(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    with pytest.raises(ValueError):
        Watchdog(store, config={})
    watchdog = Watchdog(
        store,
        config=config(
            execution_ledger=str(tmp_path / "missing.sqlite3"), execution_ledger_required=True
        ),
    )
    assert watchdog.poll_once()["status"] == "blocked_configuration"
    with pytest.raises(PermissionError):
        watchdog.record_heartbeat("unconfigured-agent")
    unconfigured_required = Watchdog(
        store,
        config=config(execution_ledger_required=True),
    )
    assert unconfigured_required.poll_once()["status"] == "blocked_configuration"
    store.close()


def test_runtime_heartbeat_hook_bootstraps_before_watchdog_start(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    heartbeat = record_worker_heartbeat(store, "agent-a", "connected", worker_id="worker-process-1")
    assert heartbeat["status"] == "connected"
    assert (
        store.connection.execute(
            "SELECT worker_id FROM watchdog_heartbeats WHERE agent_id='agent-a'"
        ).fetchone()[0]
        == "worker-process-1"
    )
    watchdog = Watchdog(store, config=config())
    assert watchdog.poll_once()["findings"] == []
    store.close()
