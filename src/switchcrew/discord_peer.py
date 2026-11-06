"""Exchange explicit messages between independently configured Discord bots."""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

import httpx

DISCORD_API = "https://discord.com/api/v10/"
PREFIX = "[flip-peer:v1 to="


def snowflake(value: str, field: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value) or int(value) >= 2**64:
        raise ValueError(f"{field} must be a valid Discord ID")
    return value


def envelope(recipient: str) -> str:
    return f"{PREFIX}{recipient}]\n"


@dataclass(frozen=True)
class DiscordPeer:
    client: httpx.Client
    channel_id: str
    bot_id: str
    peer_id: str

    @property
    def messages_path(self) -> str:
        return f"channels/{self.channel_id}/messages"

    def send(self, body: str, *, nonce: str | None = None) -> dict:
        content = envelope(self.peer_id) + body
        if not body.strip() or len(content.encode("utf-16-le")) // 2 > 2000:
            raise ValueError("Message must be nonempty and fit Discord's 2000-character limit")
        payload = {"content": content, "allowed_mentions": {"parse": []}}
        if nonce is not None:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,25}", nonce):
                raise ValueError("Nonce must be 1–25 letters, digits, underscores or hyphens")
            payload.update(nonce=nonce, enforce_nonce=True)
        response = self.client.post(self.messages_path, json=payload)
        response.raise_for_status()
        return {"message_id": response.json()["id"]}

    def inbox(self, *, before: str | None = None) -> dict:
        params = {"limit": "100"}
        if before is not None:
            params["before"] = before
        response = self.client.get(self.messages_path, params=params)
        response.raise_for_status()
        page = response.json()
        prefix = envelope(self.bot_id)
        messages = []
        for message in page:
            author = message.get("author") or {}
            content = message.get("content", "")
            if (
                message.get("channel_id") == self.channel_id
                and not message.get("webhook_id")
                and author.get("bot") is True
                and author.get("id") == self.peer_id
                and content.startswith(prefix)
                and content[len(prefix) :].strip()
            ):
                messages.append({"message_id": message["id"], "body": content[len(prefix) :]})
        # Use the last raw message so a page of unrelated traffic is still pageable.
        return {"messages": messages, "next_before": page[-1]["id"] if len(page) == 100 else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send", help="Send the text supplied on stdin")
    send.add_argument("--nonce", help="Reuse this ID when retrying the same message")
    inbox = commands.add_parser("inbox", help="Read one page of peer messages as JSON")
    inbox.add_argument("--before", help="Read the older page identified by next_before")
    args = parser.parse_args()

    try:
        channel_var = "FLIP_PEER_DISCORD_CHANNEL_ID"
        peer_var = "FLIP_PEER_DISCORD_PEER_BOT_ID"
        channel_id = snowflake(os.environ.get(channel_var, ""), channel_var)
        peer_id = snowflake(os.environ.get(peer_var, ""), peer_var)
        before = None
        if args.command == "inbox" and args.before is not None:
            before = snowflake(args.before, "--before")
        token = os.environ.get("FLIP_PEER_DISCORD_BOT_TOKEN", "").strip()
        if not token or any(char.isspace() for char in token):
            raise ValueError("Set FLIP_PEER_DISCORD_BOT_TOKEN to the local bot token")
        body = sys.stdin.read(2001) if args.command == "send" else None
        with httpx.Client(
            base_url=DISCORD_API,
            headers={"Authorization": f"Bot {token}"},
            trust_env=False,
        ) as client:
            identity = client.get("users/@me")
            identity.raise_for_status()
            user = identity.json()
            bot_id = user.get("id", "")
            if user.get("bot") is not True or bot_id == peer_id:
                raise ValueError("Configure two distinct Discord bots")
            relay = DiscordPeer(client, channel_id, snowflake(bot_id, "Local bot ID"), peer_id)
            result = (
                relay.send(body, nonce=args.nonce)
                if body is not None
                else relay.inbox(before=before)
            )
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        retry_after = error.response.headers.get("Retry-After", "")
        detail = (
            f" (retry after {retry_after} seconds)"
            if status == 429 and retry_after.isascii() and retry_after.replace(".", "", 1).isdigit()
            else ""
        )
        parser.exit(1, f"Discord returned HTTP {status}{detail}\n")
    except httpx.RequestError:
        parser.exit(1, "Discord request failed. Check connectivity before retrying.\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
