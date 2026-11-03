"""Actual artifact analyses with source bounds; no external action doubles needed."""

import hashlib
import json

import pytest

from switchcrew.graphs import build_agent_graph, invoke_agent_graph
from switchcrew.specialists import SPECIALIST_ROLES, specialist_executor


def state(role, **changes):
    return {
        "agent_role": role,
        "task_id": "task",
        "handoff_id": "handoff",
        "question": "Configure Switch installation and recovery",
        "acceptance_criteria": ["Document Switch setup and recovery"],
        **changes,
    }


def artifact(tmp_path, content, name="guide.md"):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return {"root": str(tmp_path), "sources": [name]}


@pytest.mark.parametrize("role", ["community", "documentation", "knowledge", "onboarding"])
async def test_text_specialists_return_actual_cited_artifacts(tmp_path, role):
    content = "# Switch setup\nInstall Switch dependencies.\nConfigure the recovery worker.\n"
    config = artifact(tmp_path, content)
    executor = specialist_executor(role, config)
    result = await invoke_agent_graph(build_agent_graph(role, executor=executor), state(role))
    result = result["result"]
    assert result["status"] == "completed"
    assert result["action_performed"] is False
    assert "Switch" in result["artifact"]["content"]
    assert "[S1:L" in result["artifact"]["content"]
    assert result["sources"][0]["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert all(c["status"] == "human_review_required" for c in result["acceptance_assessment"])
    if role == "onboarding":
        assert len(result["steps"]) == 2
    if role == "knowledge":
        assert result["entries"][0]["source"] == "S1"


async def test_no_evidence_is_explicit_in_support_draft(tmp_path):
    executor = specialist_executor("community", artifact(tmp_path, "Unrelated content."))
    result = await executor(state("community"))
    assert result["needs_clarification"]
    assert "No relevant source excerpt" in result["artifact"]["content"]


async def test_security_reports_locations_without_secret_values(tmp_path):
    secret = "ghp_abcdefghijklmnop123456"
    content = (
        f"API_KEY={secret}\nsubprocess.run(args, shell=True)\nrequests.get(url, verify=False)\n"
    )
    config = artifact(tmp_path, content, "snippet.txt")
    result = await specialist_executor("security", config)(state("security"))
    assert result["status"] == "completed"
    assert [f["line"] for f in result["findings"]] == [1, 2, 3]
    assert result["sources"][0]["redacted_lines"] == [1]
    assert secret not in json.dumps(result)
    assert "possible_secret" in result["artifact"]["content"]


async def test_analytics_computes_metrics_from_actual_records(tmp_path):
    config = artifact(
        tmp_path,
        {
            "records": [
                {"status": "completed", "duration_seconds": 10},
                {"status": "failed", "duration_seconds": 30},
                {"status": "completed"},
            ]
        },
        "metrics.json",
    )
    result = await specialist_executor("analytics", config)(state("analytics"))
    assert result["metrics"] == {
        "record_count": 3,
        "status_counts": {"completed": 2, "failed": 1},
        "duration_sample_count": 2,
        "mean_duration_seconds": 20,
    }


@pytest.mark.parametrize("duration", [-1, True, "20", float("nan"), float("inf")])
async def test_analytics_refuses_invalid_durations(tmp_path, duration):
    config = artifact(tmp_path, {"records": [{"duration_seconds": duration}]}, "metrics.json")
    result = await specialist_executor("analytics", config)(state("analytics"))
    assert result["status"] == "blocked_configuration"


async def test_scheduling_matches_timezones_and_accepts_touching_boundaries(tmp_path):
    config = artifact(
        tmp_path,
        {
            "events": [
                {
                    "title": "Overlapping",
                    "start": "2026-11-02T10:00:00+05:30",
                    "end": "2026-11-02T11:00:00+05:30",
                },
                {"title": "After", "start": "2026-11-02T05:30:00Z", "end": "2026-11-02T06:30:00Z"},
            ]
        },
        "calendar.json",
    )
    config["candidate"] = {
        "title": "Review",
        "start": "2026-11-02T04:45:00Z",
        "end": "2026-11-02T05:30:00Z",
    }
    result = await specialist_executor("scheduling", config)(state("scheduling"))
    proposal = result["proposal"]
    assert not proposal["available_in_snapshot"]
    assert [c["title"] for c in proposal["conflicts"]] == ["Overlapping"]
    assert result["action_performed"] is False
    config["candidate"]["start"] = "2026-11-02T08:00:00"
    assert (await specialist_executor("scheduling", config)(state("scheduling")))[
        "status"
    ] == "blocked_configuration"


async def test_moderation_uses_operator_rules_whole_words_and_returns_review_only(tmp_path):
    config = artifact(
        tmp_path,
        {
            "messages": [
                {"id": "one", "text": "Buy SPAM now"},
                {"id": "two", "text": "spammer archive"},
            ]
        },
        "messages.json",
    )
    config["rules"] = [{"id": "unsolicited", "terms": ["spam"]}]
    result = await specialist_executor("moderation", config)(state("moderation"))
    assert result["reviews"] == [
        {
            "message_id": "one",
            "rule_id": "unsolicited",
            "source": "S1",
            "recommendation": "human_review",
        }
    ]
    assert result["action_performed"] is False


async def test_incident_timeline_sorts_events_without_inventing_root_cause(tmp_path):
    config = artifact(
        tmp_path,
        {
            "events": [
                {
                    "timestamp": "2026-11-02T05:00:00Z",
                    "severity": "error",
                    "message": "Worker timeout",
                },
                {
                    "timestamp": "2026-11-02T04:50:00Z",
                    "severity": "info",
                    "message": "Deployment started",
                },
            ]
        },
        "events.json",
    )
    result = await specialist_executor("incident", config)(state("incident"))
    assert result["failure_count"] == 1
    assert result["root_cause"] == "unconfirmed"
    content = result["artifact"]["content"]
    assert content.index("Deployment started") < content.index("Worker timeout")


@pytest.mark.parametrize(
    "source", ["../outside.txt", ".env", ".switch/config/local.json", "/etc/passwd", "secret.key"]
)
async def test_sources_cannot_escape_root_or_read_private_configuration(tmp_path, source):
    result = await specialist_executor(
        "documentation", {"root": str(tmp_path), "sources": [source]}
    )(state("documentation"))
    assert result["status"] == "blocked_configuration"
    assert "passwd" not in json.dumps(result)


async def test_symlink_escape_and_oversized_source_are_blocked(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("external")
    (root / "link.md").symlink_to(tmp_path / "outside.txt")
    config = {"root": str(root), "sources": ["link.md"]}
    assert (await specialist_executor("documentation", config)(state("documentation")))[
        "status"
    ] == "blocked_configuration"
    (root / "large.md").write_text("x" * 65537)
    config["sources"] = ["large.md"]
    assert (await specialist_executor("documentation", config)(state("documentation")))[
        "status"
    ] == "blocked_configuration"


async def test_request_cannot_override_operator_sources(tmp_path):
    config = artifact(tmp_path, "Configure Switch using the verified setup guide.")
    result = await specialist_executor("documentation", config)(
        state("documentation", sources=["/etc/passwd"], root="/", rules=[{"action": "ban"}])
    )
    assert result["status"] == "completed"
    assert result["sources"][0]["path"] == "guide.md"
    assert "/etc/passwd" not in json.dumps(result)


def test_factory_cannot_substitute_privileged_roles():
    for role in {"engineering", "qa", "release", "pm", "voice"}:
        with pytest.raises(ValueError):
            specialist_executor(role, {})
    assert len(SPECIALIST_ROLES) == 9
