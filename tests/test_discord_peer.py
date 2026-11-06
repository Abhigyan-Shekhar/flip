import io
import json
import sys

import httpx
import pytest

from switchcrew import discord_peer


def test_two_bots_exchange_messages_through_cli(monkeypatch, capsys):
    messages = []
    client = httpx.Client

    def discord(request):
        token = request.headers["Authorization"]
        bot_id = {"Bot local-a": "11", "Bot local-b": "22"}[token]
        author = {"id": bot_id, "bot": True}
        if request.url.path == "/api/v10/users/@me":
            return httpx.Response(200, json=author)
        assert request.url.path == "/api/v10/channels/123/messages"
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["allowed_mentions"] == {"parse": []}
            assert payload["nonce"] == "attempt-1"
            assert payload["enforce_nonce"] is True
            message = {
                "id": str(101 + len(messages)),
                "channel_id": "123",
                "author": author,
                "content": payload["content"],
            }
            messages.insert(0, message)
            return httpx.Response(200, json=message)
        return httpx.Response(200, json=messages)

    monkeypatch.setattr(
        discord_peer.httpx,
        "Client",
        lambda **kw: client(transport=httpx.MockTransport(discord), **kw),
    )
    monkeypatch.setenv("FLIP_PEER_DISCORD_CHANNEL_ID", "123")

    def run(token, peer_id, *args, body=""):
        monkeypatch.setenv("FLIP_PEER_DISCORD_BOT_TOKEN", token)
        monkeypatch.setenv("FLIP_PEER_DISCORD_PEER_BOT_ID", peer_id)
        monkeypatch.setattr(sys, "argv", ["discord_peer", *args])
        monkeypatch.setattr(sys, "stdin", io.StringIO(body))
        discord_peer.main()
        return json.loads(capsys.readouterr().out)

    assert run("local-a", "22", "send", "--nonce", "attempt-1", body="Can you review this?") == {
        "message_id": "101"
    }
    messages.extend(
        [
            {**messages[0], "id": "99", "author": {"id": "33", "bot": True}},
            {**messages[0], "id": "98", "webhook_id": "55"},
            {**messages[0], "id": "97", "content": "[flip-peer:v1 to=33]\nFor someone else"},
            {**messages[0], "id": "96", "channel_id": "456"},
        ]
    )
    assert run("local-b", "11", "inbox") == {
        "messages": [{"message_id": "101", "body": "Can you review this?"}],
        "next_before": None,
    }
    assert run("local-b", "11", "send", "--nonce", "attempt-1", body="Yes, send the diff.") == {
        "message_id": "106"
    }
    assert run("local-a", "22", "inbox")["messages"] == [
        {"message_id": "106", "body": "Yes, send the diff."}
    ]


def test_inbox_can_page_past_unrelated_messages():
    def discord(request):
        assert request.url.params["before"] == "300"
        page = [{"id": str(message_id)} for message_id in range(299, 199, -1)]
        return httpx.Response(200, json=page)

    with httpx.Client(
        base_url=discord_peer.DISCORD_API, transport=httpx.MockTransport(discord)
    ) as client:
        relay = discord_peer.DiscordPeer(client, "123", "11", "22")
        assert relay.inbox(before="300") == {"messages": [], "next_before": "200"}
        with pytest.raises(ValueError, match="2000-character"):
            relay.send("😀" * 1000)
