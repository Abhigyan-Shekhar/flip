#!/usr/bin/env python3
"""Prepare private configuration and systemd units; never enroll agents or install units."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def private_env(root: Path) -> None:
    path = root / ".env"
    if path.is_symlink():
        raise ValueError("Refusing a symlink .env")
    if path.exists():
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Existing .env must be a private regular file (chmod 600 .env)")
        print("Preserved existing .env; no credentials changed")
        return
    content = (root / ".env.example").read_text()
    if content.splitlines().count("SWITCHCREW_API_TOKEN=") != 1:
        raise ValueError("Template must contain exactly one blank API token")
    content = content.replace(
        "SWITCHCREW_API_TOKEN=\n", f"SWITCHCREW_API_TOKEN={secrets.token_urlsafe(32)}\n", 1
    )
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as output:
        output.write(content)
    print("Created private .env with a new API token; fill in real Switch/Discord identities")


def quoted(value: str, *, command: bool = True) -> str:
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Paths must not contain control characters")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + (value.replace("$", "$$") if command else value) + '"'


def units(root: Path, user: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", user) or user == "root":
        raise ValueError("Use a dedicated nonroot operator username")
    root = root.resolve()
    python = quoted(str(root / ".venv/bin/python"))
    helper = quoted(str(root / "deploy/hosting/hosting.py"))
    local = quoted(str(root / "deploy/switch/local.sh"))
    common = f"User={user}\nWorkingDirectory={quoted(str(root), command=False)}\nUMask=0077\n"
    result = {
        "flip-switch.service": (
            "[Unit]\nDescription=Flip pinned Switch infrastructure\n"
            "Requires=docker.service\nAfter=network-online.target docker.service\n"
            "Wants=network-online.target\n\n[Service]\nType=oneshot\n"
            + common
            + f"ExecStart=/bin/bash {local} up\nExecStop=/bin/bash {local} stop\n"
            "RemainAfterExit=yes\nTimeoutStartSec=1800\nTimeoutStopSec=120\n\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )
    }
    for role in ("api", "pm", "research"):
        dependency = "flip-switch.service" if role == "api" else "flip-api.service"
        command = (
            "-m uvicorn switchcrew.api:create_app --factory --host 127.0.0.1 "
            "--port 8000 --no-access-log"
            if role == "api"
            else f"-m switchcrew.workers {role}"
        )
        result[f"flip-{role}.service"] = (
            f"[Unit]\nDescription=Flip {role}\nRequires={dependency}\n"
            f"After=network-online.target {dependency}\nWants=network-online.target\n"
            "StartLimitIntervalSec=0\n\n[Service]\nType=simple\n"
            + common
            + f"ExecStartPre={python} {helper} check-core\nExecStart={python} {command}\n"
            "Restart=on-failure\nRestartSec=5\nNoNewPrivileges=yes\n"
            "ProtectSystem=full\n\n[Install]\nWantedBy=multi-user.target\n"
        )
    return result


def check_core() -> int:
    from dotenv import dotenv_values

    from switchcrew.deployment_preflight import inspect_settings
    from switchcrew.settings import Settings

    settings = Settings()
    required = {
        "api_auth",
        "core_switch_identity",
        "core_switch_credentials",
        "discord_ingress",
        "env_file_permissions",
        "credential_separation",
    }
    blocked = [
        check["name"]
        for check in inspect_settings(settings)["checks"]
        if check["name"] in required and check["status"] != "ready"
    ]
    shared = dotenv_values(".env")
    if json.loads(shared.get("SWITCHCREW_APPROVAL_CREDENTIALS") or "{}"):
        blocked.append("approval_credentials_must_be_api_only")
    if not settings.research_source_urls:
        blocked.append("research_source_urls")
    print("Core configuration: " + ("blocked: " + ", ".join(blocked) if blocked else "ready"))
    print("Live Discord, managed Codex, approvals and draft PR acceptance remain unverified")
    return 2 if blocked else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("check-core")
    render = sub.add_parser("render")
    render.add_argument("--user", required=True)
    render.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            private_env(ROOT)
            (ROOT / "data").mkdir(mode=0o700, exist_ok=True)
        elif args.command == "render":
            rendered = units(ROOT, args.user)
            args.output.mkdir(parents=True, exist_ok=True)
            for name, content in rendered.items():
                (args.output / name).write_text(content)
            print(
                f"Rendered {len(rendered)} services into {args.output}; "
                "nothing installed or started"
            )
        else:
            os.chdir(ROOT)
            return check_core()
    except ValueError:
        # Settings validation errors can include input values. Never echo them.
        print("Configuration rejected. Check private file permissions, settings and arguments.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
