"""Exercise deployment boundaries without touching real credentials or systemd."""

import importlib.util
import json
import stat
from pathlib import Path

import pytest

from switchcrew.settings import Settings

SPEC = importlib.util.spec_from_file_location(
    "flip_hosting", Path(__file__).resolve().parents[1] / "deploy/hosting/hosting.py"
)
hosting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hosting)


def test_init_creates_private_token_and_never_rotates_existing_config(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("SWITCHCREW_API_TOKEN=\nOTHER=preserved\n")
    hosting.private_env(tmp_path)
    env = tmp_path / ".env"
    original = env.read_text()
    token = original.splitlines()[0].split("=", 1)[1]
    assert len(token) >= 32
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "OTHER=preserved" in original
    hosting.private_env(tmp_path)
    assert env.read_text() == original
    assert token not in capsys.readouterr().out


def test_init_rejects_symlink_and_public_existing_configuration(tmp_path):
    target = tmp_path / "private"
    target.write_text("existing")
    env = tmp_path / ".env"
    env.symlink_to(target)
    with pytest.raises(ValueError):
        hosting.private_env(tmp_path)
    env.unlink()
    env.write_text("existing")
    env.chmod(0o644)
    with pytest.raises(ValueError):
        hosting.private_env(tmp_path)
    assert env.read_text() == "existing"


def test_units_keep_one_database_directory_and_gate_only_native_services(tmp_path):
    root = tmp_path / "space %n $ROOT"
    units = hosting.units(root, "flip_operator")
    assert len(units) == 4
    for name, unit in units.items():
        assert "User=flip_operator\n" in unit
        assert 'WorkingDirectory="' + str(root).replace("%", "%%") + '"' in unit
        assert "EnvironmentFile=" not in unit
        if name != "flip-switch.service":
            assert "check-core\n" in unit
            assert "Restart=on-failure" in unit
            assert "$$ROOT" in unit
        else:
            assert "Requires=docker.service" in unit
            assert "RemainAfterExit=yes" in unit
    assert "--host 127.0.0.1" in units["flip-api.service"]


@pytest.mark.parametrize("user", ["root", "operator\nUser=root", "--root"])
def test_units_reject_root_and_unit_injection(tmp_path, user):
    with pytest.raises(ValueError):
        hosting.units(tmp_path, user)


def core_settings(**overrides):
    values = dict(
        api_token="a" * 40,
        pm_api_key="pm-secret",
        research_api_key="research-secret",
        pm_agent_id="pm",
        research_agent_id="research",
        pm_participant_id="pm-participant",
        research_participant_id="research-participant",
        switch_room_id="room",
        discord_guild_ids=["guild"],
        discord_user_ids=["user"],
        discord_bindings=[
            dict(
                room_id="room",
                bridge_id="bridge",
                guild_id="guild",
                channel_id="channel",
                participant_id="human",
                user_id="user",
            )
        ],
        research_source_urls=["https://discord.com/developers/docs/intro"],
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_core_gate_allows_research_without_faking_privileged_readiness(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("switchcrew.settings.Settings", lambda: core_settings())
    assert hosting.check_core() == 0
    output = capsys.readouterr().out
    assert "unverified" in output
    assert "secret" not in output


def test_core_gate_blocks_unbound_discord_and_shared_approver_secrets(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("SWITCHCREW_APPROVAL_CREDENTIALS=" + json.dumps({"human": "private-secret"}))
    env.chmod(0o600)
    monkeypatch.setattr("switchcrew.settings.Settings", lambda: core_settings(discord_bindings=[]))
    assert hosting.check_core() == 2
    output = capsys.readouterr().out
    assert "discord_ingress" in output
    assert "approval_credentials_must_be_api_only" in output
    assert "private-secret" not in output


def test_core_gate_accepts_api_only_approver_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "switchcrew.settings.Settings",
        lambda: core_settings(approval_credentials={"human": "h" * 40}),
    )
    assert hosting.check_core() == 0
