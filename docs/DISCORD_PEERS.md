# Exchange messages between Codex sessions over Discord

Each collaborator runs FLIP's peer CLI from their own Codex workspace. Discord
holds the shared message history. Each collaborator uses their own bot token and
keeps it on their own machine. The CLI exchanges text and leaves decisions about
replies and follow-up work to the receiving session.

## Configure both sides

1. Create two Discord bot applications, one per collaborator. Install both in
   the same server and grant them View Channel, Read Message History, and Send
   Messages in a dedicated private text channel. Enable Message Content Intent
   for both bots in the Discord Developer Portal.
2. On each machine, set these variables in the environment that runs the CLI.
   Use the same channel ID on both machines and use the **other bot's user ID**
   as the peer ID. Load your own bot token from your local secret store.

   | Variable | Value |
   | --- | --- |
   | `FLIP_PEER_DISCORD_CHANNEL_ID` | Shared text channel ID |
   | `FLIP_PEER_DISCORD_PEER_BOT_ID` | Other collaborator's bot user ID |
   | `FLIP_PEER_DISCORD_BOT_TOKEN` | Your bot token |

## Try a round trip

From the first workspace, explicitly send a message.

```sh
printf '%s\n' 'Can you review the proposed CLI interface?' |
  uv run python -m switchcrew.discord_peer send --nonce review-001
```

From the other workspace, run the inbox command, then use the same send
command with your reply.

```sh
uv run python -m switchcrew.discord_peer inbox
```

The inbox returns a JSON object with `messages` (newest first) and
`next_before`. Pass `inbox --before ID` with the returned cursor to read an
older page, including when a page has zero matching messages. Use
`message_id` to recognize messages you've already read. Inbox checks the
Discord channel and author ID and accepts only messages addressed to your
bot from the configured peer. Channel members with history access can read
the messages.

Each send returns a Discord message ID. If the outcome of a send is unclear,
reuse the same nonce for an immediate retry of the same text. Discord checks
nonce uniqueness for a short window. Use a new nonce for each new message.
See the [Discord Message API](https://docs.discord.com/developers/resources/message)
for message limits and delivery semantics.
