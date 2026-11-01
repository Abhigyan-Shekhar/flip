#!/usr/bin/env python3
"""Read-only host/configuration preflight. Never prints configuration values."""

import argparse
import json
import os
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCALARS = (
    "API_TOKEN",
    "SWITCH_ROOM_ID",
    "PM_AGENT_ID",
    "RESEARCH_AGENT_ID",
    "PM_API_KEY",
    "RESEARCH_API_KEY",
    "PM_PARTICIPANT_ID",
    "RESEARCH_PARTICIPANT_ID",
)
ARRAYS = ("DISCORD_GUILD_IDS", "DISCORD_USER_IDS", "DISCORD_BINDINGS", "RESEARCH_SOURCE_URLS")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_config(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    values.update(os.environ)
    return values


def command_ok(args):
    try:
        return (
            subprocess.run(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def valid_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
        return (
            bool(parsed.hostname)
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        )
    except ValueError:
        return False


def probe(url, api_key=None):
    if not valid_url(url):
        return {"status": "invalid_url"}
    try:
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {api_key}"} if api_key else {}
        )
        with urllib.request.build_opener(NoRedirect).open(request, timeout=3) as response:
            return {"status": "reachable", "http_status": response.status}
    except urllib.error.HTTPError as error:
        return {"status": "http_error", "http_status": error.code}
    except (OSError, ValueError):
        return {"status": "unreachable"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--switch-url", default=None)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:3001")
    parser.add_argument("--crew-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        values = read_config(args.env_file)
    except (OSError, UnicodeError):
        print(json.dumps({"configuration": {"status": "unreadable"}}))
        return 1
    missing, invalid = [], []
    decoded = {}
    for key in SCALARS + ARRAYS:
        name = "SWITCHCREW_" + key
        value = values.get(name, "").strip()
        if not value:
            missing.append(name)
            continue
        if key in ARRAYS:
            try:
                parsed = json.loads(value)
                decoded[key] = parsed
                if not isinstance(parsed, list):
                    invalid.append(name)
                elif not parsed:
                    missing.append(name)
                elif key == "DISCORD_BINDINGS":
                    required = (
                        "room_id",
                        "bridge_id",
                        "guild_id",
                        "channel_id",
                        "participant_id",
                        "user_id",
                    )
                    if any(
                        not isinstance(item, dict)
                        or any(
                            not isinstance(item.get(field), str) or not item[field].strip()
                            for field in required
                        )
                        for item in parsed
                    ):
                        invalid.append(name)
                elif any(not isinstance(item, str) or not item.strip() for item in parsed):
                    invalid.append(name)
                elif key == "RESEARCH_SOURCE_URLS" and any(
                    not valid_url(item) or not item.startswith("https://") for item in parsed
                ):
                    invalid.append(name)
            except (ValueError, TypeError):
                invalid.append(name)
        elif key == "API_TOKEN" and len(value) < 32:
            invalid.append(name)
    for suffix in ("AGENT_ID", "PARTICIPANT_ID"):
        pm, research = (
            values.get("SWITCHCREW_" + role + "_" + suffix) for role in ("PM", "RESEARCH")
        )
        if pm and pm == research:
            invalid.extend("SWITCHCREW_" + role + "_" + suffix for role in ("PM", "RESEARCH"))
    bindings = decoded.get("DISCORD_BINDINGS", [])
    guilds = decoded.get("DISCORD_GUILD_IDS", [])
    users = decoded.get("DISCORD_USER_IDS", [])
    guilds = guilds if isinstance(guilds, list) else []
    users = users if isinstance(users, list) else []
    if isinstance(bindings, list) and "SWITCHCREW_DISCORD_BINDINGS" not in invalid:
        for binding in bindings:
            if (
                binding["room_id"] != values.get("SWITCHCREW_SWITCH_ROOM_ID")
                or binding["guild_id"] not in guilds
                or binding["user_id"] not in users
            ):
                invalid.append("SWITCHCREW_DISCORD_BINDINGS")
                break
    switch_url = args.switch_url or values.get(
        "SWITCHCREW_SWITCH_BASE_URL", "http://127.0.0.1:8080"
    )
    if not valid_url(switch_url):
        invalid.append("SWITCHCREW_SWITCH_BASE_URL")
    local_runtime_required = valid_url(switch_url) and urllib.parse.urlsplit(
        switch_url
    ).hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    docker = shutil.which("docker") is not None
    runtime = {
        "docker_cli": docker,
        "docker_engine": docker and command_ok(["docker", "info"]),
        "compose": docker and command_ok(["docker", "compose", "version"]),
    }
    probes = {
        "switch_readiness": probe(switch_url.rstrip("/") + "/health/ready"),
        "gateway": probe(args.gateway_url),
        "crew_health": probe(args.crew_url.rstrip("/") + "/health"),
    }
    authentication = {}
    for role in ("PM", "RESEARCH"):
        agent_id = values.get(f"SWITCHCREW_{role}_AGENT_ID", "")
        api_key = values.get(f"SWITCHCREW_{role}_API_KEY", "")
        if agent_id and api_key:
            agent_path = urllib.parse.quote(agent_id, safe="")
            authentication[role.lower()] = probe(
                switch_url.rstrip("/") + f"/agents/{agent_path}/ops", api_key
            )
    protected = not args.env_file.exists() or not (
        stat.S_IMODE(args.env_file.stat().st_mode) & 0o077
    )
    result = {
        "runtime": runtime,
        "local_deployment_runtime_required": local_runtime_required,
        "configuration": {
            "env_file_present": args.env_file.exists(),
            "env_file_private": protected,
            "missing": missing,
            "invalid": invalid,
            "credential_authentication": authentication,
        },
        "services": probes,
        "operator_checks": {
            key: "not_verified"
            for key in (
                "discord_bot_intents_and_permissions",
                "owner_link",
                "identity_bindings",
                "agent_roles_and_addressing",
                "original_thread_workflow",
                "live_recovery",
            )
        },
    }
    print(json.dumps(result, indent=2))
    return int(
        bool(
            missing
            or invalid
            or not protected
            or (local_runtime_required and not all(runtime.values()))
            or any(item.get("http_status") != 200 for item in probes.values())
            or any(item.get("http_status") != 200 for item in authentication.values())
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
