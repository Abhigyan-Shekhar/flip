"""Controlled protocol/policy tests; real Console acceptance is recorded separately."""

import copy
import sys
from unittest.mock import AsyncMock

import httpx
import pytest

from switchcrew.managed_console import ManagedConsoleCodex


@pytest.fixture
def managed(tmp_path):
    for name in ("state", "home", "checkout"):
        (tmp_path / name).mkdir()
    (tmp_path / "socket").touch()
    config = {
        "agent_id": "61417b1c-0197-4cb7-8b94-54a4caaa413e",
        "controller_id": "8d030ed5-73b1-4cf6-95b7-88765e7adef0",
        "room_id": "1d8c9f05-5e7e-41b9-aead-bfd1b10b927d",
        "agent_name": "test-codex",
        "revision": 2,
        "container_name": "test-container",
        "network_name": "test-internal",
        "image_id": "sha256:" + "a" * 64,
        "docker_binary": sys.executable,
        "daemon_socket": str(tmp_path / "socket"),
        "state_root": str(tmp_path / "state"),
        "home_root": str(tmp_path / "home"),
    }
    switch = type("Switch", (), {"base_url": "http://127.0.0.1:8080"})()
    agent = ManagedConsoleCodex(config, switch, "b" * 32)
    checkout = tmp_path / "checkout"
    assignment = {
        "agents": [
            {
                "agent_id": config["agent_id"],
                "revision": 2,
                "desired_state": "running",
                "definition": {
                    "provider": "codex",
                    "name": "test-codex",
                    "directory": str(checkout),
                    "isolation": "isolated",
                    "auto_approve": False,
                    "instructions": "switchcrew-assignment:" + "b" * 32,
                },
            }
        ]
    }
    container = {
        "State": {"Running": True},
        "Image": config["image_id"],
        "Config": {
            "User": "501:501",
            "Env": [
                "NODE_VERSION=22.24.0",
                "HTTPS_PROXY=http://flip-codex-egress:3128",
                "HTTP_PROXY=http://flip-codex-egress:3128",
            ],
        },
        "HostConfig": {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "PortBindings": {},
            "IpcMode": "private",
            "Memory": 1024**3,
            "NanoCpus": 1000000000,
            "PidsLimit": 256,
            "Dns": ["127.0.0.1"],
        },
        "NetworkSettings": {"Networks": {"test-internal": {}}},
        "Mounts": [
            {"Type": "bind", "Destination": dest, "Source": source, "RW": True}
            for dest, source in {
                str(checkout): str(checkout),
                "/state": config["state_root"],
                "/home/agent": config["home_root"],
            }.items()
        ],
    }
    agent.assignment = AsyncMock(return_value=assignment)

    async def docker(*args):
        return [{"Internal": True}] if args[0] == "network" else [container]

    agent._docker = AsyncMock(side_effect=docker)
    agent._control = AsyncMock(return_value={"state": "connected", "placements": {}})
    return agent, checkout, assignment, container


async def test_correct_binding_and_isolation_accept_without_creating_session(managed):
    agent, checkout, _, _ = managed
    await agent.validate(checkout)
    assert agent._docker.call_count == 2
    assert all(call.args[0] != "exec" for call in agent._docker.call_args_list)


async def test_warmed_health_maps_session_ids_to_room_ids(managed):
    agent, checkout, _, _ = managed
    agent._control.return_value = {
        "state": "connected",
        "placements": {"real-session": agent.config["room_id"]},
    }
    await agent.validate(checkout)
    agent._control.return_value["placements"]["real-session"] = "another-room"
    with pytest.raises(ValueError, match="another room"):
        await agent.validate(checkout)


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "claude"),
        ("directory", "/another"),
        ("isolation", "shared"),
        ("auto_approve", True),
        ("instructions", ""),
    ],
)
async def test_wrong_managed_definition_blocks_before_container_control(managed, field, value):
    agent, checkout, assignment, _ = managed
    assignment["agents"][0]["definition"][field] = value
    with pytest.raises(ValueError, match="definition"):
        await agent.validate(checkout)
    agent._docker.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("Privileged", True),
        ("ReadonlyRootfs", False),
        ("CapDrop", []),
        ("SecurityOpt", []),
        ("Memory", 0),
        ("Dns", []),
        ("PortBindings", {"22/tcp": [{}]}),
    ],
)
async def test_weakened_container_policy_fails_closed(managed, field, value):
    agent, checkout, _, container = managed
    container["HostConfig"][field] = value
    with pytest.raises(ValueError, match="isolation"):
        await agent.validate(checkout)


@pytest.mark.parametrize(
    "violation",
    [
        "extra_mount",
        "different_source",
        "credential",
        "node_override",
        "external_network",
        "revision",
        "other_agent",
    ],
)
async def test_scope_and_identity_cannot_expand(managed, violation):
    agent, checkout, assignment, container = managed
    if violation == "extra_mount":
        container["Mounts"].append(
            {"Type": "bind", "Destination": "/host", "Source": "/", "RW": True}
        )
    elif violation == "different_source":
        container["Mounts"][0]["Source"] = "/another"
    elif violation == "credential":
        container["Config"]["Env"].append("GITHUB_TOKEN=fixture")
    elif violation == "node_override":
        container["Config"]["Env"].append("NODE_OPTIONS=--require=/workspace/override.js")
    elif violation == "external_network":
        container["NetworkSettings"]["Networks"]["bridge"] = {}
    elif violation == "revision":
        assignment["agents"][0]["revision"] = 3
    else:
        assignment["agents"].append(copy.deepcopy(assignment["agents"][0]))
    with pytest.raises(ValueError):
        await agent.validate(checkout)


async def test_real_session_ids_and_ambiguous_sessions_are_checked(managed):
    agent, _, _, _ = managed
    agent._control = AsyncMock(return_value=[])
    assert await agent.find("b" * 32) is None
    session = {
        "agentId": agent.agent_id,
        "sessionId": "real-session",
        "roomIds": [agent.config["room_id"]],
    }
    agent._control.return_value = [session]
    assert await agent.find("b" * 32) == "real-session"
    agent._control.return_value = [session, session]
    with pytest.raises(ValueError):
        await agent.find("b" * 32)
    agent._control.return_value = None
    with pytest.raises(ValueError):
        await agent.create("b" * 32)
    with pytest.raises(ValueError):
        await agent.create("c" * 32)


async def test_assignment_uses_scoped_controller_token_not_owner_credentials(managed, monkeypatch):
    agent, _, assignment, _ = managed
    secret = agent.state / "secrets/controller-credential"
    secret.parent.mkdir()
    secret.write_text("fixture-controller-secret")
    secret.chmod(0o600)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Switch-Controller-Protocol"] == "2"
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "fixture-access-token"})
        assert request.headers["Authorization"] == "Bearer fixture-access-token"
        return httpx.Response(200, json=assignment)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    assert await ManagedConsoleCodex.assignment(agent) == assignment
    assert len(requests) == 2
