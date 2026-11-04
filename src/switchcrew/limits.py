"""Durable fixed-window intake budgets; duplicate event processing is free."""

import time


def consume_budget(store, subject, event_id, *, limit=120, window_seconds=60, now=None):
    bucket = int(time.time() if now is None else now) // window_seconds
    with store._lock, store.transaction():
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS intake_budget (subject TEXT, event_id TEXT, "
            "bucket INTEGER, PRIMARY KEY(subject,event_id))"
        )
        old = store.connection.execute(
            "SELECT 1 FROM intake_budget WHERE subject=? AND event_id=?", (subject, event_id)
        ).fetchone()
        if old:
            return True
        count = store.connection.execute(
            "SELECT count(*) FROM intake_budget WHERE subject=? AND bucket=?", (subject, bucket)
        ).fetchone()[0]
        if count >= limit:
            return False
        store.connection.execute("DELETE FROM intake_budget WHERE bucket < ?", (bucket - 60,))
        store.connection.execute(
            "INSERT INTO intake_budget VALUES(?,?,?)", (subject, event_id, bucket)
        )
        return True
