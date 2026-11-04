import pytest

from switchcrew.deployment_preflight import (
    REQUIRED_LIVE_CHECKS,
    inspect_settings,
    verify_evidence_manifest,
)
from switchcrew.settings import Settings


def test_preflight_cannot_claim_live_acceptance_from_static_config(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "state.sqlite3",
        api_token="a" * 40,
        pm_api_key="b" * 40,
        research_api_key="c" * 40,
        pm_agent_id="pm",
        research_agent_id="research",
        pm_participant_id="pm-participant",
        research_participant_id="research-participant",
        switch_room_id="room",
        engineering_agent_id="eng",
        qa_agent_id="qa",
        reviewer_agent_id="reviewer",
        release_agent_id="release",
        release_repository="owner/project",
        release_base="main",
        approval_credentials={"human": "z" * 40},
    )
    report = inspect_settings(settings)
    assert report["status"] == "ready_for_live_checks"
    assert all(value == "not_verified" for value in report["live_acceptance"].values())
    assert report["checks"]
    assert "a" * 40 not in str(report)


def test_preflight_blocks_incomplete_identities_and_public_operator_config(tmp_path):
    config = tmp_path / "operator.json"
    config.write_text("{}")
    config.chmod(0o644)
    report = inspect_settings(Settings(_env_file=None, harness_config=config))
    assert report["status"] == "blocked_configuration"
    assert next(c for c in report["checks"] if c["name"] == "harness_config")["status"] == "blocked"


def test_evidence_contract_rejects_missing_receipts_and_unsafe_paths():
    document = {
        "host": "verification-mac",
        "commit": "a" * 40,
        "checks": {name: {"status": "not_run", "receipt": ""} for name in REQUIRED_LIVE_CHECKS},
    }
    assert verify_evidence_manifest(document)["status"] == "live_pending"
    document["checks"]["switch_health"] = {"status": "passed", "receipt": ""}
    with pytest.raises(ValueError, match="receipt"):
        verify_evidence_manifest(document)
    document["checks"]["switch_health"]["receipt"] = "../private/token"
    with pytest.raises(ValueError, match="within"):
        verify_evidence_manifest(document)
