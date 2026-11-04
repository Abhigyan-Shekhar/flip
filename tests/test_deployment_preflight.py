import pytest

from switchcrew.deployment_preflight import (
    REQUIRED_LIVE_CHECKS,
    inspect_settings,
    verify_evidence_manifest,
)
from switchcrew.settings import Settings


def _ready_settings(tmp_path, **overrides):
    values = {
        "database_path": tmp_path / "state.sqlite3",
        "api_token": "a" * 40,
        "pm_api_key": "b" * 40,
        "research_api_key": "c" * 40,
        "pm_agent_id": "pm",
        "research_agent_id": "research",
        "pm_participant_id": "pm-participant",
        "research_participant_id": "research-participant",
        "switch_room_id": "room",
        "discord_guild_ids": ["guild"],
        "discord_user_ids": ["user"],
        "discord_bindings": [
            {
                "room_id": "room",
                "bridge_id": "bridge",
                "guild_id": "guild",
                "channel_id": "channel",
                "participant_id": "discord-participant",
                "user_id": "user",
            }
        ],
        "engineering_agent_id": "eng",
        "qa_agent_id": "qa",
        "reviewer_agent_id": "reviewer",
        "release_agent_id": "release",
        "release_repository": "owner/project",
        "release_base": "main",
        "approval_credentials": {"human": "z" * 40},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_preflight_cannot_claim_live_acceptance_from_static_config(tmp_path):
    report = inspect_settings(_ready_settings(tmp_path))
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"discord_bindings": []},
        {"discord_guild_ids": []},
        {"discord_user_ids": []},
        {
            "discord_bindings": [
                {
                    "room_id": "other-room",
                    "bridge_id": "bridge",
                    "guild_id": "guild",
                    "channel_id": "channel",
                    "participant_id": "discord-participant",
                    "user_id": "user",
                }
            ]
        },
    ],
)
def test_preflight_blocks_missing_or_mismatched_discord_ingress(tmp_path, overrides):
    report = inspect_settings(_ready_settings(tmp_path, **overrides))
    assert report["status"] == "blocked_configuration"
    assert (
        next(c for c in report["checks"] if c["name"] == "discord_ingress")["status"] == "blocked"
    )


def test_preflight_requires_private_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DUMMY=1")
    env_file.chmod(0o644)
    settings = _ready_settings(tmp_path)
    report = inspect_settings(settings, env_file=env_file)
    assert report["status"] == "blocked_configuration"
    assert (
        next(c for c in report["checks"] if c["name"] == "env_file_permissions")["status"]
        == "blocked"
    )
    env_file.chmod(0o600)
    assert inspect_settings(settings, env_file=env_file)["status"] == "ready_for_live_checks"


@pytest.mark.parametrize(
    ("overrides", "check_name"),
    [
        ({"pm_api_key": ""}, "core_switch_credentials"),
        ({"pm_api_key": "c" * 40}, "core_switch_credentials"),
        ({"approval_credentials": {"human": "a" * 40}}, "credential_separation"),
        ({"human_credentials": {"human": "h" * 40}}, "credential_separation"),
    ],
)
def test_preflight_blocks_invalid_or_duplicate_credentials(tmp_path, overrides, check_name):
    report = inspect_settings(_ready_settings(tmp_path, **overrides))
    assert report["status"] == "blocked_configuration"
    assert next(c for c in report["checks"] if c["name"] == check_name)["status"] == "blocked"


@pytest.mark.parametrize(
    "receipt", [" ", ".", "./", "receipts/", "../token", r"..\\token", r"C:\\token"]
)
def test_evidence_contract_rejects_non_file_receipt_placeholders(receipt):
    document = {
        "host": "verification-mac",
        "commit": "a" * 40,
        "checks": {
            name: {"status": "passed", "receipt": f"receipts/{name}.json"}
            for name in REQUIRED_LIVE_CHECKS
        },
    }
    assert verify_evidence_manifest(document)["status"] == "operator_claims_complete"
    document["checks"]["switch_health"]["receipt"] = receipt
    with pytest.raises(ValueError):
        verify_evidence_manifest(document)
