import json
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

from switchcrew.discord_interactions import respond_to_interaction, slash_manifest
from switchcrew.settings import Settings
from switchcrew.store import Store
from switchcrew.switch import SwitchClient


def setup(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = (
        private.public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        .hex()
    )
    settings = Settings(
        _env_file=None,
        discord_application_public_key=public,
        discord_guild_ids=["guild"],
        discord_user_ids=["alice"],
        discord_bindings=[
            {
                "room_id": "room",
                "bridge_id": "bridge",
                "guild_id": "guild",
                "channel_id": "thread",
                "participant_id": "participant",
                "user_id": "alice",
            }
        ],
    )
    store = Store(tmp_path / "tasks.sqlite3")
    task = store.create_task(
        "Explain the test", {"room_id": "room", "thread_id": "thread"}, ["Evidence"]
    )
    return private, settings, store, task


def signed(private, document, now=None):
    payload = json.dumps(document).encode()
    timestamp = str(int((now or datetime.now(UTC)).timestamp()))
    signature = private.sign(timestamp.encode() + payload).hex()
    return payload, {"x-signature-timestamp": timestamp, "x-signature-ed25519": signature}


def test_signed_slash_command_is_scoped_and_read_only(tmp_path):
    private, settings, store, task = setup(tmp_path)
    request = {
        "type": 2,
        "id": "interaction",
        "guild_id": "guild",
        "channel_id": "thread",
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "crew",
            "options": [{"name": "status", "options": [{"name": "task_id", "value": task["id"]}]}],
        },
    }
    payload, headers = signed(private, request)
    response = respond_to_interaction(store, settings, payload, headers)
    assert response["type"] == 4
    assert response["data"]["flags"] == 64
    assert "DRAFT" in response["data"]["content"]
    assert len(slash_manifest()[0]["options"]) == 6
    request["member"]["user"]["id"] = "mallory"
    payload, headers = signed(private, request)
    with pytest.raises(PermissionError, match="authorized"):
        respond_to_interaction(store, settings, payload, headers)
    store.close()


def test_discord_signature_and_timestamp_are_verified(tmp_path):
    private, settings, store, _ = setup(tmp_path)
    payload, headers = signed(private, {"type": 1})
    assert respond_to_interaction(store, settings, payload, headers) == {"type": 1}
    with pytest.raises(PermissionError, match="signature"):
        respond_to_interaction(store, settings, payload + b" ", headers)
    headers["x-signature-timestamp"] = "1000000000"
    with pytest.raises(PermissionError, match="Expired"):
        respond_to_interaction(store, settings, payload, headers)
    store.close()


@pytest.mark.parametrize(
    ("role", "viewer", "room", "status", "expected"),
    [
        ("pm", "alice", "room", 200, "atlassian"),
        ("pm", "alice", "room", 404, "Couldn't read"),
        ("pm", "alice", "room", 204, "Couldn't read"),
        ("pm", "someone-else", "room", None, None),
        ("pm", "alice", "other-room", None, None),
        ("research", "alice", "room", None, None),
    ],
)
@pytest.mark.asyncio
async def test_agent_connections_reads_only_for_authorized_viewers(
    tmp_path, monkeypatch, role, viewer, room, status, expected
):
    from switchcrew import discord_interactions

    private, settings, store, _ = setup(tmp_path)
    settings.switch_room_id = room
    settings.pm_agent_id = "pm-agent-id"
    settings.pm_api_key = SecretStr("test-key")
    settings.discord_connection_viewers = {"pm": [viewer]}
    calls = []

    def grants(request):
        calls.append(request)
        if status == 204:
            return httpx.Response(204)
        return httpx.Response(
            status,
            json={
                "grants": [
                    {
                        "service": "atlassian",
                        "mcp_servers": [{"headers": {"Authorization": "hidden-header"}}],
                    },
                    {"service": "google-workspace", "mcp_servers": []},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(grants)) as http:
        monkeypatch.setattr(
            discord_interactions,
            "SwitchClient",
            lambda base_url, agent_id, api_key: SwitchClient(
                base_url, agent_id, api_key, client=http
            ),
        )
        request = {
            "type": 2,
            "guild_id": "guild",
            "channel_id": "thread",
            "member": {"user": {"id": "alice"}},
            "data": {"name": "agent-connections", "options": [{"name": "agent", "value": role}]},
        }
        payload, headers = signed(private, request)
        if status is None:
            with pytest.raises(PermissionError, match="scope"):
                await discord_interactions.respond_to_interaction_async(
                    store, settings, payload, headers
                )
            assert not calls
        else:
            response = await discord_interactions.respond_to_interaction_async(
                store, settings, payload, headers
            )
            assert len(calls) == 1
            assert calls[0].method == "GET"
            assert calls[0].url.path == "/agents/pm-agent-id/service-grants"
            assert calls[0].headers["authorization"] == "Bearer test-key"
            assert response["type"] == 4
            assert response["data"]["flags"] == 64
            content = response["data"]["content"]
            assert expected in content
            assert "switchdash://open/connections" in content
            assert "hidden-header" not in content
            assert "google-workspace" not in content
    store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"member": {"user": ["alice"]}},
        {"member": ["alice"]},
        {"data": ["crew"]},
        {"data": {"name": "crew", "options": 1}},
        {"data": {"name": "crew", "options": {"status": {}}}},
    ],
)
def test_malformed_signed_command_returns_client_error(tmp_path, changes):
    from fastapi.testclient import TestClient

    from switchcrew.api import create_app

    private, settings, store, _ = setup(tmp_path)
    settings.database_path = tmp_path / "http.sqlite3"
    settings.api_token = SecretStr("test-api-token-" * 3)
    document = {
        "type": 2,
        "guild_id": "guild",
        "channel_id": "thread",
        "member": {"user": {"id": "alice"}},
        "data": {"name": "crew", "options": []},
        **changes,
    }
    payload, headers = signed(private, document)
    with TestClient(create_app(settings)) as client:
        response = client.post("/discord/interactions", content=payload, headers=headers)
        assert response.status_code == 400
    store.close()


def thread_request(task_id):
    return {
        "type": 2,
        "id": "interaction-thread",
        "guild_id": "guild",
        "channel_id": "discord-thread",
        "channel": {"id": "discord-thread", "type": 11, "parent_id": "thread"},
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "crew",
            "options": [{"name": "status", "options": [{"name": "task_id", "value": task_id}]}],
        },
    }


def configure_thread(settings):
    settings.discord_thread_bindings = [
        {
            "guild_id": "guild",
            "channel_id": "thread",
            "thread_id": "discord-thread",
            "room_id": "room",
            "switch_thread_id": "sw-root",
        }
    ]


def test_real_discord_thread_uses_verified_switch_root(tmp_path):
    private, settings, store, _ = setup(tmp_path)
    configure_thread(settings)
    task = store.create_task(
        "Scoped task", {"room_id": "room", "message_id": "sw-root"}, ["Evidence"]
    )
    payload, headers = signed(private, thread_request(task["id"]))
    response = respond_to_interaction(store, settings, payload, headers)
    assert response["data"]["flags"] == 64
    assert f"Task {task['id']}: DRAFT" in response["data"]["content"]
    assert store.get_task(task["id"])["version"] == task["version"]
    store.close()


@pytest.mark.parametrize("change", ["missing", "duplicate", "parent", "room", "identity"])
def test_thread_mapping_fails_closed(tmp_path, change):
    private, settings, store, task = setup(tmp_path)
    configure_thread(settings)
    request = thread_request(task["id"])
    if change == "missing":
        settings.discord_thread_bindings = []
    elif change == "duplicate":
        settings.discord_thread_bindings *= 2
    elif change == "parent":
        request["channel"]["parent_id"] = "other-parent"
    elif change == "room":
        settings.discord_thread_bindings[0]["room_id"] = "other-room"
    elif change == "identity":
        request["channel"]["id"] = "other-thread"
    payload, headers = signed(private, request)
    with pytest.raises(PermissionError):
        respond_to_interaction(store, settings, payload, headers)
    store.close()


def test_verified_thread_cannot_read_other_thread_task(tmp_path):
    private, settings, store, _ = setup(tmp_path)
    configure_thread(settings)
    task = store.create_task(
        "Private other thread", {"room_id": "room", "thread_id": "sw-other-root"}, ["Evidence"]
    )
    payload, headers = signed(private, thread_request(task["id"]))
    response = respond_to_interaction(store, settings, payload, headers)
    assert "Private other thread" not in response["data"]["content"]
    assert "DRAFT" not in response["data"]["content"]
    store.close()


def test_launch_site_posts_ready_preview_once_from_signed_discord_request(tmp_path, monkeypatch):
    import base64

    from fastapi.testclient import TestClient

    from switchcrew.api import create_app

    private, settings, store, _ = setup(tmp_path)
    store.close()
    settings.database_path = tmp_path / "http.sqlite3"
    settings.api_token = SecretStr("test-api-token-" * 3)
    settings.switch_room_id = "room"
    settings.discord_site_publishers = ["alice"]
    settings.vercel_token = SecretStr("test-vercel-token")
    settings.vercel_preview_project = "switchcrew-demo"
    settings.vercel_team_id = "team_demo"
    calls = []

    def vendor(request):
        calls.append(request)
        if request.method == "POST":
            assert request.url.host == "api.vercel.com"
            assert request.url.path == "/v13/deployments"
            assert request.url.params["teamId"] == "team_demo"
            assert request.headers["authorization"] == "Bearer test-vercel-token"
            body = json.loads(request.content)
            assert body["name"] == "switchcrew-demo"
            assert "target" not in body
            (file,) = body["files"]
            assert file["file"] == "index.html"
            html = base64.b64decode(file["data"]).decode()
            assert '<h1 id="title">Night Shift &amp; Co.</h1>' in html
            assert "&lt;script&gt;" in html
            assert "<script>" not in html
            return httpx.Response(200, json={"id": "dpl_demo", "readyState": "BUILDING"})
        if request.method == "GET":
            assert request.url.path == "/v13/deployments/dpl_demo"
            return httpx.Response(
                200, json={"id": "dpl_demo", "readyState": "READY", "url": "demo.vercel.app"}
            )
        assert request.url.host == "discord.com"
        assert request.url.path == "/api/v10/webhooks/456/callback-token/messages/@original"
        assert "authorization" not in request.headers
        assert "https://demo.vercel.app" in json.loads(request.content)["content"]
        return httpx.Response(200)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(vendor), **kwargs),
    )
    document = {
        "type": 2,
        "id": "123",
        "guild_id": "guild",
        "channel_id": "thread",
        "application_id": "456",
        "token": "callback-token",
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "launch-site",
            "options": [
                {"name": "title", "value": "Night Shift & Co."},
                {"name": "description", "value": "<script> is printed as text."},
            ],
        },
    }
    payload, headers = signed(private, document)
    with TestClient(create_app(settings)) as client:
        settings.discord_site_publishers = []
        denied = client.post("/discord/interactions", content=payload, headers=headers)
        assert denied.status_code == 401
        assert not calls

        settings.discord_site_publishers = ["alice"]
        response = client.post("/discord/interactions", content=payload, headers=headers)
        assert response.json() == {"type": 5, "data": {"flags": 64}}
        duplicate = client.post("/discord/interactions", content=payload, headers=headers)
        assert duplicate.json()["data"]["content"] == "This Discord request was already accepted."

    assert [call.method for call in calls] == ["POST", "GET", "PATCH"]


@pytest.mark.asyncio
async def test_build_site_uses_previous_source_for_a_scoped_revision(tmp_path, monkeypatch):
    from starlette.background import BackgroundTasks

    from switchcrew import discord_interactions

    private, settings, store, _ = setup(tmp_path)
    settings.switch_room_id = "room"
    settings.discord_site_publishers = ["alice"]
    settings.vercel_token = SecretStr("test-vercel-token")
    settings.vercel_preview_project = "switchcrew-demo"
    first = "<!doctype html><html><body>Coffee</body></html>"
    revised = "<!doctype html><html><body>Coffee Menu</body></html>"
    prompts, uploads, messages = [], [], []

    class Builder:
        async def run_json(self, spec, prompt):
            prompts.append(prompt)
            return {"html": first if len(prompts) == 1 else revised}

    async def deploy(client, *, page, generated, **kwargs):
        assert generated
        uploads.append(page)
        return f"https://demo-{len(uploads)}.vercel.app"

    async def reply(application_id, token, content):
        messages.append(content)

    monkeypatch.setattr(discord_interactions, "deploy_html", deploy)
    monkeypatch.setattr(discord_interactions, "_edit_interaction_reply", reply)
    document = {
        "type": 2,
        "id": "123",
        "guild_id": "guild",
        "channel_id": "thread",
        "application_id": "456",
        "token": "callback-token",
        "member": {"user": {"id": "alice"}},
        "data": {"name": "build-site", "options": [{"name": "prompt", "value": "A coffee site"}]},
    }

    async def invoke():
        background = BackgroundTasks()
        payload, headers = signed(private, document)
        response = await discord_interactions.respond_to_interaction_async(
            store,
            settings,
            payload,
            headers,
            site_builder=(Builder(), None),
            background_tasks=background,
        )
        assert response == {"type": 5, "data": {"flags": 64}}
        await background()

    await invoke()
    document["id"] = "124"
    document["data"]["options"] = [
        {"name": "prompt", "value": "Add a menu"},
        {"name": "revision", "value": "123"},
    ]
    await invoke()
    assert uploads == [first, revised]
    assert first in prompts[1]
    assert "Add a menu" in prompts[1]
    assert "https://demo-2.vercel.app" in messages[1]
    assert "Revision `124`" in messages[1]
    assert store.site_preview_source("124", "room", "alice") == revised
    assert store.site_preview_source("124", "room", "someone-else") is None
    assert store.site_preview_source("124", "another-room", "alice") is None
    store.close()


@pytest.mark.asyncio
async def test_generated_site_deploys_with_static_content_policy():
    import base64

    from switchcrew.vercel import deploy_html

    def vercel(request):
        assert request.method == "POST"
        payload = json.loads(request.content)
        files = {
            entry["file"]: base64.b64decode(entry["data"]).decode() for entry in payload["files"]
        }
        assert files["index.html"] == "<!doctype html><html></html>"
        (route,) = json.loads(files["vercel.json"])["headers"]
        (header,) = route["headers"]
        assert header["key"] == "Content-Security-Policy"
        assert "default-src 'none'" in header["value"]
        assert "form-action 'none'" in header["value"]
        assert "sandbox" in header["value"]
        return httpx.Response(
            200, json={"id": "dpl_demo", "readyState": "READY", "url": "demo.vercel.app"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(vercel)) as client:
        assert (
            await deploy_html(
                client,
                token="test",
                project="demo",
                team_id="",
                page="<!doctype html><html></html>",
                generated=True,
            )
            == "https://demo.vercel.app"
        )
