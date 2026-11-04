import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from switchcrew.api import create_app
from switchcrew.settings import Settings

PROJECT = "github.com/acme/service"
ROOM = "switch-room"
API_TOKEN = "x" * 40
ACTORS = {
    "alice": {"projects": [PROJECT]},
    "bob": {"projects": [PROJECT]},
    "release-manager": {"projects": [PROJECT]},
}


def configured_app(tmp_path):
    import_root = tmp_path / "imports"
    import_root.mkdir()
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    organization = tmp_path / "organization.json"
    organization.write_text(
        json.dumps(
            {
                "meetings": {
                    "input_root": str(recordings),
                    "notes_agent_id": "notes-agent",
                    "voice_agent_id": "voice-agent",
                    "origins": {
                        meeting_id: {
                            "room_id": ROOM,
                            "channel_id": "discord-channel",
                            "thread_id": "discord-thread",
                            "message_id": f"{meeting_id}-source-message",
                            "guild_id": "guild-1",
                            "owner_id": owner,
                        }
                        for meeting_id, owner in {
                            "identity-test": "alice",
                            "forgery-test": "alice",
                            "cross-meeting": "alice",
                            "owner-check-meeting": "alice",
                            "roster-check-meeting": "alice",
                        }.items()
                    },
                    "recording_manifest": {
                        "reserved.wav": {
                            "meeting_id": "reserved-meeting",
                            "owner_id": "alice",
                            "participants": ["alice", "bob"],
                        },
                        "bob-owned.wav": {
                            "meeting_id": "owner-check-meeting",
                            "owner_id": "bob",
                            "participants": ["alice", "bob"],
                        },
                        "roster-mismatch.wav": {
                            "meeting_id": "roster-check-meeting",
                            "owner_id": "alice",
                            "participants": ["alice"],
                        },
                    },
                },
                "knowledge": {
                    "import_roots": [str(import_root)],
                    "trusted_actors": ACTORS,
                },
            }
        )
    )
    organization.chmod(0o600)
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.sqlite3",
        api_token=API_TOKEN,
        human_credentials={"alice": "a" * 40, "bob": "b" * 40},
        approval_credentials={"release-manager": "m" * 40},
        organization_config=organization,
        switch_room_id=ROOM,
        memory_project=PROJECT,
        pm_agent_id="pm-agent",
        research_agent_id="research-agent",
        specialist_targets={
            "notes": {"agent_id": "notes-agent"},
            "research": {"agent_id": "research-agent"},
        },
    )
    return create_app(settings), import_root, recordings


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_human_identity_cannot_impersonate_privileged_actor_or_release(tmp_path):
    app, _, _ = configured_app(tmp_path)
    with TestClient(app) as client:
        service_headers = bearer(API_TOKEN)
        task = client.post(
            "/tasks",
            headers=service_headers,
            json={"objective": "Release proof", "acceptance_criteria": ["Review"]},
        ).json()

        denied = client.post(
            f"/tasks/{task['id']}/approvals",
            headers=bearer("a" * 40),
            json={"stage": "release", "approved": True, "expected_version": 0},
        )
        assert denied.status_code == 403

        impersonation = client.post(
            f"/tasks/{task['id']}/approvals",
            headers=bearer("a" * 40),
            json={
                "stage": "release",
                "approved": True,
                "expected_version": 0,
                "actor": "release-manager",
            },
        )
        assert impersonation.status_code in {403, 422}

        created = client.post(
            "/meetings",
            headers=bearer("a" * 40),
            json={
                "meeting_id": "identity-test",
                "channel_id": "discord-channel",
                "thread_id": "discord-thread",
                "participants": ["alice", "bob"],
                "source": {"room_id": ROOM},
            },
        )
        assert created.status_code == 200
        assert created.json()["created_by"] == "alice"
        assert created.json()["source"]["user_id"] == "alice"

        body_actor = client.post(
            "/meetings/identity-test/consent",
            headers=bearer("a" * 40),
            json={
                "evidence_id": "alice-consent",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "disclosure_acknowledged": True,
                "actor": "bob",
            },
        )
        assert body_actor.status_code == 422


def test_meeting_creation_rejects_forged_registered_origin_before_persisting(tmp_path):
    app, _, _ = configured_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/meetings",
            headers=bearer("a" * 40),
            json={
                "meeting_id": "forgery-test",
                "channel_id": "forged-channel",
                "thread_id": "forged-thread",
                "participants": ["alice", "bob"],
                "source": {"room_id": "attacker-room", "message_id": "forged-message"},
            },
        )
        assert response.status_code == 403
        assert (
            app.state.store.connection.execute(
                "SELECT COUNT(*) FROM meetings WHERE id='forgery-test'"
            ).fetchone()[0]
            == 0
        )


def test_optional_organization_capabilities_are_disabled_without_operator_config(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "disabled.sqlite3",
        api_token=API_TOKEN,
        human_credentials={"alice": "a" * 40},
        switch_room_id=ROOM,
        memory_project=PROJECT,
    )
    with TestClient(create_app(settings)) as client:
        auth = bearer("a" * 40)
        meeting = client.post(
            "/meetings",
            headers=auth,
            json={
                "meeting_id": "disabled-meeting",
                "channel_id": "channel",
                "participants": ["alice"],
                "source": {"room_id": ROOM},
            },
        )
        assert meeting.status_code == 409
        knowledge = client.post(
            "/knowledge/search",
            headers=auth,
            json={"query": "anything", "project_id": PROJECT},
        )
        assert knowledge.status_code == 409


def test_recording_manifest_denies_cross_meeting_file_and_unmatched_owner(tmp_path):
    app, _, recordings = configured_app(tmp_path)
    (recordings / "reserved.wav").touch()
    (recordings / "bob-owned.wav").touch()
    (recordings / "roster-mismatch.wav").touch()
    with TestClient(app) as client:
        auth = bearer("a" * 40)
        for meeting_id in (
            "cross-meeting",
            "owner-check-meeting",
            "roster-check-meeting",
        ):
            created = client.post(
                "/meetings",
                headers=auth,
                json={
                    "meeting_id": meeting_id,
                    "channel_id": "discord-channel",
                    "thread_id": "discord-thread",
                    "participants": ["alice", "bob"],
                    "source": {"room_id": ROOM},
                },
            )
            assert created.status_code == 200, created.text
            for actor, token in (("alice", "a" * 40), ("bob", "b" * 40)):
                consent = client.post(
                    f"/meetings/{meeting_id}/consent",
                    headers=bearer(token),
                    json={
                        "evidence_id": f"{meeting_id}-{actor}",
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                        "disclosure_acknowledged": True,
                    },
                )
                assert consent.status_code == 200, consent.text

        cross_meeting = client.post(
            "/meetings/cross-meeting/recording",
            headers=auth,
            json={"filename": "reserved.wav"},
        )
        owner_mismatch = client.post(
            "/meetings/owner-check-meeting/recording",
            headers=auth,
            json={"filename": "bob-owned.wav"},
        )
        roster_mismatch = client.post(
            "/meetings/roster-check-meeting/recording",
            headers=auth,
            json={"filename": "roster-mismatch.wav"},
        )
        assert cross_meeting.status_code == 403
        assert owner_mismatch.status_code == 403
        assert roster_mismatch.status_code == 403
        assert (
            app.state.store.connection.execute(
                "SELECT COUNT(*) FROM meeting_recording_jobs"
            ).fetchone()[0]
            == 0
        )


def test_personal_knowledge_routes_keep_owner_and_approver_boundaries(tmp_path):
    app, import_root, _ = configured_app(tmp_path)
    (import_root / "conventions.md").write_text(
        "Use stable revision pinned evidence for normalized identifiers.", encoding="utf-8"
    )
    with TestClient(app) as client:
        imported = client.post(
            "/knowledge/import",
            headers=bearer("a" * 40),
            json={
                "source": "conventions.md",
                "project_id": PROJECT,
                "scope": "personal",
                "source_owner": "Alice's engineering notes",
            },
        )
        assert imported.status_code == 200, imported.text
        document_id = imported.json()["id"]
        approved = client.post(f"/knowledge/{document_id}/approve", headers=bearer("a" * 40))
        assert approved.status_code == 200

        bob_approve = client.post(f"/knowledge/{document_id}/approve", headers=bearer("b" * 40))
        assert bob_approve.status_code == 403

        alice_search = client.post(
            "/knowledge/search",
            headers=bearer("a" * 40),
            json={"query": "revision evidence", "project_id": PROJECT},
        )
        assert alice_search.status_code == 200
        assert [item["id"] for item in alice_search.json()["documents"]] == [document_id]

        bob_search = client.post(
            "/knowledge/search",
            headers=bearer("b" * 40),
            json={"query": "revision evidence", "project_id": PROJECT},
        )
        assert bob_search.status_code == 200
        assert bob_search.json()["documents"] == []

        body_actor = client.post(
            "/knowledge/search",
            headers=bearer("b" * 40),
            json={"query": "revision evidence", "project_id": PROJECT, "actor_id": "alice"},
        )
        assert body_actor.status_code == 422
