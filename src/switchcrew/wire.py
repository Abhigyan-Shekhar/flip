"""Evidence-reference envelopes for Switch transport; durable payloads remain in SQLite."""

import hashlib

from .store import encode


def transport_payload(payload):
    if (
        payload.get("type") not in {"handoff.accepted", "handoff.completed"}
        or "result" not in payload
    ):
        return payload
    result = payload.get("result")
    return {key: value for key, value in payload.items() if key != "result"} | {
        "result_status": result.get("status") if isinstance(result, dict) else None,
        "result_sha256": hashlib.sha256(encode(result).encode()).hexdigest(),
        "evidence_location": "durable_handoff_record",
    }


def result_matches(message, handoff):
    if message.get("type") != "handoff.completed":
        return True
    digest = message.get("result_sha256")
    return (
        digest is None
        or digest == hashlib.sha256(encode(handoff.get("result")).encode()).hexdigest()
    )
