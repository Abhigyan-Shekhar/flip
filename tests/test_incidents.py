from switchcrew.discord import DiscordSource
from switchcrew.incidents import IncidentIntake
from switchcrew.store import Store


def source(message_id, body, *, user="u1", channel="c1", room="r1", thread=None, guild="g1"):
    return DiscordSource(guild, channel, user, room, user, message_id, thread, body, ())


def test_filters_classification_and_severity():
    store = Store(":memory:")
    intake = IncidentIntake(store, {"c1"}, clock=lambda: 600)
    assert intake.ingest(source("m0", "hello everyone")) is None
    report = intake.ingest(source("m1", "Payments are failing for some users"))
    assert report["incident"]["classification"] == "bug_report"
    report = intake.ingest(source("m2", "Production outage: payments are down for all users"))
    assert report["created"]
    incident = report["incident"]
    assert incident["category"] == "service_failure"
    assert incident["severity"] == "SEV1"
    assert incident["confidence"] == "unconfirmed"
    assert incident["needs_engineering"] is True


def test_dedup_thread_followup_multiple_people_and_idempotency():
    store = Store(":memory:")
    intake = IncidentIntake(store, {"c1"}, clock=lambda: 600)
    first = intake.ingest(source("m1", "Checkout timeout error", thread="t1"))
    assert first["created"]
    repeat = intake.ingest(source("m1", "Checkout timeout error", thread="t1"))
    assert repeat["created"] is False
    followup = intake.ingest(source("m2", "Any update?", user="u2", thread="t1"))
    assert followup["created"] is False
    assert followup["incident"]["task_id"] == first["task_id"]
    assert len(followup["incident"]["sources"]) == 2
    assert len(store.list_events(first["task_id"])) == 3


def test_dedupe_scope_cancel_and_rate_limit():
    store = Store(":memory:")
    now = [1200]
    intake = IncidentIntake(store, {"c1"}, clock=lambda: now[0], max_events_per_minute=1)
    one = intake.ingest(source("m1", "API crash", thread="t1"))
    assert intake.ingest(source("m2", "API crash", thread="t1")) is None
    # Channel wide limit prevents many allowed users from multiplying admission.
    assert intake.ingest(source("m2", "API crash", user="u2", thread="t1")) is None
    now[0] += 60
    same = intake.ingest(source("m2", "API crash", user="u2", thread="t1"))
    assert same["incident_id"] == one["incident_id"]
    now[0] += 60
    other_room = intake.ingest(source("m3", "API crash", room="r2", user="u3"))
    assert other_room["incident_id"] != one["incident_id"]
    task = store.get_task(one["task_id"])
    store.transition_task(task["id"], "CANCELLED", task["version"])
    now[0] += 60
    fresh = intake.ingest(source("m4", "API crash", thread="t1", user="u4"))
    assert fresh["incident_id"] != one["incident_id"]


def test_allowlist_bounds_and_synthetic_label():
    store = Store(":memory:")
    intake = IncidentIntake(store, {"c1"}, clock=lambda: 1)
    assert intake.ingest(source("m1", "Database broken", channel="c2")) is None
    assert intake.ingest(source("m2", "Database broken" + "x" * 10001)) is None
    got = intake.ingest(source("m3", "Synthetic fixture: database broken"))
    assert got["incident"]["synthetic"] == 1
    assert got["incident"]["evidence_refs"][0].endswith(":m3")


def test_thread_reply_anchors_top_level_report_and_normalized_dedupe():
    store = Store(":memory:")
    intake = IncidentIntake(store, {"c1"}, clock=lambda: 600)
    first = intake.ingest(source("root-message", "Payments are failing for some users"))
    assert first["incident"]["task"]["source"]["thread_id"] == "root-message"
    reply = intake.ingest(
        source(
            "reply-message", "Some users report payments failed", thread="root-message", user="u2"
        )
    )
    assert reply["created"] is False
    assert reply["task_id"] == first["task_id"]


def test_concurrent_connections_admit_duplicate_identity_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "concurrent.db"
    stores = [Store(path), Store(path)]
    intakes = [IncidentIntake(item, {"c1"}) for item in stores]
    event = source("same-message", "API crash")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda intake: intake.ingest(event), intakes))
    assert sum(result["created"] for result in results) == 1
    assert results[0]["task_id"] == results[1]["task_id"]
    assert len(stores[0].connection.execute("SELECT id FROM tasks").fetchall()) == 1


def test_retained_source_refs_capped_and_rate_survives_reopen(tmp_path):
    path = tmp_path / "retention.db"
    store = Store(path)
    intake = IncidentIntake(store, {"c1"}, clock=lambda: 1200, max_events_per_minute=150)
    first = intake.ingest(source("m0", "Database timeout"))
    for number in range(1, 105):
        intake.ingest(source(f"m{number}", "Database timeout", user=f"u{number}"))
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM incident_sources WHERE incident_id=?",
            (first["incident_id"],),
        ).fetchone()[0]
        == 100
    )
    assert len(intake.get(first["task_id"])["sources"]) == 100

    rate_db = tmp_path / "rate.db"
    first_store = Store(rate_db)
    first_intake = IncidentIntake(first_store, {"c1"}, clock=lambda: 1800, max_events_per_minute=1)
    assert first_intake.ingest(source("limited1", "API crash"))
    first_store.close()
    reopened_store = Store(rate_db)
    reopened = IncidentIntake(reopened_store, {"c1"}, clock=lambda: 1800, max_events_per_minute=1)
    assert reopened.ingest(source("limited2", "API crash", user="u2")) is None


def test_normalized_reports_deduplicate_without_thread_subscription():
    store = Store(":memory:")
    intake = IncidentIntake(store, {"c1"}, clock=lambda: 600)
    first = intake.ingest(source("report-first", "Payments are failing for some users"))
    other = intake.ingest(source("report-second", "Some users report payments failed", user="u2"))
    assert other["task_id"] == first["task_id"]
    assert other["created"] is False
    injection = intake.ingest(source("security-report", "Possible SQL injection in the API"))
    assert injection["incident"]["category"] == "security"
    store.close()
