# Switch server and Discord setup

## Repeatable local deployment

Check this checkout's host before relying on a previous deployment record:

```sh
python3 deploy/switch/preflight.py
```

This read-only check reports Docker/Compose availability, local HTTP health and
missing or structurally invalid required configuration fields. It never prints
configuration values. URLs can be overridden with `--switch-url`,
`--gateway-url`, and `--crew-url`; probes reject URL credentials and redirects.
The check also authenticates both worker keys through operation discovery.
Exit 0 means these prerequisites passed; it does not verify Discord permissions,
identity ownership, addressing, workflow or recovery.
Optional product Waggle is not required. Environment variables override the
root `.env`; the lightweight env reader supports literal key/value lines and
outer quotes, without shell evaluation or variable interpolation.

With Docker Desktop running, from the SwitchCrew repository:

```sh
bash deploy/switch/local.sh up
bash deploy/switch/local.sh ps
curl --fail http://127.0.0.1:8080/health/ready
```

The script checks out the pinned source below in ignored `.switch/server`,
generates a mode-0600 `.env` once, and builds the official server and gateway.
Subsequent starts preserve credentials and PostgreSQL data. It refuses an
unexpected source revision. PostgreSQL uses loopback port 5433; the gateway uses
3001 to avoid existing services on port 3000. Long-running containers restart
with Docker, and Compose waits for database and Switch readiness.
Use `bash deploy/switch/local.sh stop` to stop without removing data; `logs`
and `ps` inspect the stack. Docker Desktop must itself start after reboot.

Connect Switch Console using **Connect to an existing server**:

| Setting | Value |
| --- | --- |
| Gateway URL | `http://127.0.0.1:3001` |
| API URL | `http://127.0.0.1:8080` |
| Email | `admin@switch.local` |
| Password | `GATEWAY_ADMIN_PASSWORD` in local `.switch/server/.env` |

Open the protected env file locally to retrieve the generated password. Never
commit it, paste it into chat, or include it in command arguments or screenshots.
Switch Console **v0.40.1** is the compatible desktop release: it and this server
both declare `gateway-api` contract 1. Download the architecture-specific app
from the [official release](https://github.com/sandbox-quantum/switch/releases/tag/switch-console-v0.40.1).
For Apple Silicon, the official `switch-console-arm64.zip` SHA-256 is
`02f4f9e4da4da2f831ae618911ffcaf047a28c58c8b1d19bc64345953ef125e0`.
Console is a desktop application, separate from the gateway container.

The SwitchCrew API can run alongside this stack with
`docker compose up --build -d --wait api` after configuring its root `.env`.
PM/Research workers additionally require registration, room identities and
verified Discord bindings; starting the infrastructure does not configure them.

## Upstream provisioning reference

The Python adapter targets upstream commit
`557b62b23002548baa678339a97c8140c1ccaa8b`, agent SSE protocol 6. Current upstream
HEAD has changed transport schemas; do not float the server version. Official
source was inspected directly during implementation. This is configuration,
not a claim that a live Discord or server test has passed.

Build the official stack (Docker and Compose required; run in a separate folder):

```sh
git clone https://github.com/sandbox-quantum/switch.git switch-server
cd switch-server
git checkout 557b62b23002548baa678339a97c8140c1ccaa8b
cp .env.example .env
chmod 600 .env
bash deploy/local/generate-env-secrets.sh .env local
# Set API_HOST_PORT=8080 in .env to avoid SwitchCrew API port 8000.
# Review .env URLs, host ports and local admin account before starting.
docker compose -p switch-server -f deploy/local/standalone-docker-compose.yml \
  -f deploy/local/standalone-docker-compose.build.yml \
  --profile gateway --project-directory . up -d --build
```

This uses upstream's actual Compose/build override rather than a guessed stack.
Switch requires its own PostgreSQL; SwitchCrew SQLite is separate. Keep bind
addresses loopback unless deliberately deploying secured remote access. Do not
print `.env` or registration output. The one-time registration credential
belongs only in the registration process. Use `scripts/register_agents.py`
with `SWITCH_REGISTRATION_TOKEN` and an exclusive mode-0600 output path, once per
worker identity. It registers custom always-on workers; Codex uses Switch
Console's existing session host, not this custom worker profile.

Connect **only the built-in Switch Discord text bridge** in Console → server
Home → Messaging apps → Connect → Discord. Supply a dedicated Discord bot token
and Guild ID. Enable Server Members and Message Content intents. Invite with
`bot` and `applications.commands` scopes; grant View Channels, Send Messages,
Send Messages in Threads, Manage Webhooks, Manage Channels, Manage Roles, Read
Message History and Attach Files. Restrict bot channel visibility to the
approved guild channels. Link the human owner account. Post a normal channel
message first to create its room, then invite agents. DMs are unsupported.

For each worker, discover operations, open its stream with a stable unique
connection ID and configured rooms, claim room context, define/assume its room
role, and configure addressing policies to permit the intended agent senders.
Default owner-only addressing blocks agent-to-agent requests. Use participant
**names** for targeted operations; inspect returned `target_statuses` and require
an explicit persisted handoff acknowledgment. No API acceptance equals task
acceptance. Role leases in Switch are not publication authorization.

Bind Discord identities explicitly: `DiscordBinding` maps verified opaque
Switch participant ID + room + bridge to Discord guild/channel/user and roles.
The documented SSE envelope does not include authenticated Discord guild/user
IDs or current Discord roles. Never infer these from display names, participant
ID syntax, or JSON body claims. Configure nonempty guild and user allowlists.
Bindings require operator verification and revocation when membership changes;
role-based approval needs a separately verified current authorization source.
The normalizer preserves Switch `message_id` and `thread_id` for replies and
source references. Plain-text messages cannot approve publication.

The SSE adapter reconnects with the same connection ID and last acknowledged
cursor. Persist inbox records first, deduplicate message IDs across restarts,
then acknowledge. A gap fetches all room history pages and yields them before
new events; the worker must durably replay those pages before continuing.
Persist the returned stream cursor separately. Eviction stops the consumer to
avoid two processes fighting over the same connection ID. Outgoing mutation
requests are never blindly retried after an uncertain response.

The local deployment now generates its own infrastructure credentials. Live
agent/Discord gates remain: registration, operation discovery, Discord room
creation/owner link, role addressing,
heartbeat/reconnect/restart recovery, thread replies and actual recipient ack.

## Open the operator interface and configure rooms

The `gateway` Compose profile serves the operator web interface at
`http://127.0.0.1:3000` by default (`GATEWAY_HOST_PORT` in upstream `.env`). Sign
in with the admin account configured in `.env`; do not paste its password into
agent prompts. Desktop Console is a separate application, distributed through
[official GitHub releases](https://github.com/sandbox-quantum/switch/releases).
Choose a release compatible with this pinned server rather than silently
installing a newer transport. The upstream onboarding flow is to open Console
and ask switch-buddy to connect the server/chat app and create rooms/agents.
The Compose command starts the server and gateway, not a desktop app.

After the first `connection_state` has arrived (before that there is no live
connection), use the authenticated adapter with its connection header:

```python
await client.connect_to_room(room_id)
participants = await client.operation('list_participants')
await client.operation('define_role', name='research',
                       instructions='Return sources and limitations; no publishing.',
                       exclusive=True)
await client.operation('assume_role', role='research')
```

Run `define_role` once with room write permission. Each worker assumes only
its own role; do not run a second worker with that connection ID. Record the
participant ID from `list_participants` separately from its **name** and the
registration API **agent ID**. They serve different purposes: sender checking,
message addressing and API authentication respectively.

In Console, open each agent's settings and configure its addressing rules to
admit the other worker and the linked owner in the intended room. Verify by
sending a targeted message and seeing `target_statuses[name] == 'live'`, followed
by that worker's explicit application acknowledgment. The verified operation
`update_agent_detail(agent_id=..., addressing=...)` can change another agent
owned by the same owner, but cannot change its caller's own settings; do not
invent a policy string. Use Console's rule editor for this step and confirm
current policy via agent detail. Maintain the application allowlists even when
Switch addressing permits a request.

The implementation also checks pinned **source**, because its prose protocol
page lags additive changes: `connection_state.generation` must be echoed in
`POST /connection/beat` for protocol 6. The legacy `cursor` beat field is no
longer authoritative; resume uses `Last-Event-ID`. A heartbeat refusal with
`detail.code == 'taken_over'` is terminal, while `no_stream`/404 reopens. Tests
exercise these paths against the actual schema. `target_statuses` is a map of
participant name to reachability string, and the returned transport ID is
`event_id`. Targeted text is prefixed with mentions, so application handoff
JSON needs a delimited envelope rather than parsing the entire body as JSON.

For the host Python workers set `SWITCHCREW_SWITCH_BASE_URL=http://127.0.0.1:8080`
after changing upstream `API_HOST_PORT` as shown above. The core container still
listens on port 8000. For containerized workers on the same Docker engine, use
SwitchCrew's explicit network override from the SwitchCrew repository:

```sh
docker compose -f compose.yaml -f deploy/compose.switch-local.yaml \
  -f deploy/switch/worker-recovery.yaml --profile workers up --build -d
```

The override joins workers to the upstream `switch-server_default` network and
uses `http://switch:8000`; start upstream with project name `switch-server` first.
The local server, gateway and API have been exercised with Docker; worker
startup still requires the agent/Discord configuration described above.
The worker recovery override uses `on-failure`: normal failures restart, while
an evicted worker exits cleanly and remains stopped. Start workers explicitly
after restarting Docker. Keep this override when using a remote Switch server,
but omit the same-engine network override in that case.

Register each identity once after the bridge is reachable. Set
`SWITCH_REGISTRATION_TOKEN` locally from upstream `AGENT_REGISTRATION_TOKEN`;
never paste it into chat or command-line arguments. From SwitchCrew:

```sh
mkdir -p .switch
chmod 700 .switch
uv run python scripts/register_agents.py --url http://127.0.0.1:8080 \
  --name pm-agent --display-name 'PM Agent' --description 'Coordinates read-only research' --output .switch/pm.json
uv run python scripts/register_agents.py --url http://127.0.0.1:8080 \
  --name research-agent --display-name 'Research Agent' --description 'Retrieves configured public evidence' --output .switch/research.json
```

Transfer returned IDs/keys into the matching local settings without printing the
files. The registration script refuses to overwrite keys. Revoke orphaned
identities if registration succeeds but local key persistence fails. Unset the
registration token after provisioning; runtime workers use only their own keys.

## Live provisioning corrections

Agent machine names must use lowercase letters, digits, dots, hyphens or
underscores. Register `pm-agent` and `research-agent`; use `--display-name` for
“PM Agent” and “Research Agent”. Set `SWITCHCREW_PM_NAME=pm-agent` and
`SWITCHCREW_RESEARCH_NAME=research-agent` for targeted delivery.

The UUIDs returned by `list_participants` identify agent/external-user records.
The `payload.sender` and history `sender` fields use transport identities.
Verify those separately against stored client mappings and authenticated room
history before setting `*_PARTICIPANT_ID` or Discord bindings. Do not copy the
membership UUID into the sender allowlist or infer a transport ID from its name.

Workers assume their configured room role after each connection; define `pm`
and `research` in that room before starting them. Addressing policies must admit
the verified human external-user records and peer agent registration ID in that
room. Those policy IDs differ from transport sender IDs.

For the Gateway-intents example, configure
`https://docs.discord.com/developers/events/gateway.md`. The official HTML page
exceeds the bounded fetch size; the Markdown version is about 55 KB and is
served as `text/markdown`. The fetch limit and public-network restrictions stay
in force.

Create the Discord test thread on a human message already bridged into Switch.
A standalone thread created by the bot has no ingested root message, and this
pinned upstream can lose its thread association. Do not mark original-thread
delivery passed unless every response is observed in that thread.

Run `python3 deploy/switch/preflight.py` from the repository. It uses only the
standard library, checks private configuration, local health and authenticated
operation discovery, and never prints keys. It does not certify membership,
addressing, source retrieval, end-to-end delivery or recovery.

For the remaining request-during-gap and heartbeat gates, follow
[the recovery acceptance runbook](RECOVERY_ACCEPTANCE.md). It contains isolated
live probes and a backed-up application-cursor injection that does not edit the
Switch database. These procedures must run on the Mac with the live services;
the repository alone does not contain credentials or deploy that environment.
