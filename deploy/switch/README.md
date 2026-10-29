# Switch server and Discord setup

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

No live credentials were available. Live gates remain: `/health`, registration,
operation discovery, Discord room creation/owner link, role addressing,
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
  --profile workers up --build -d
```

The override joins workers to the upstream `switch-server_default` network and
uses `http://switch:8000`; start upstream with project name `switch-server` first.
These container commands remain unexecuted locally because Docker is absent.

Register each identity once after the bridge is reachable. Set
`SWITCH_REGISTRATION_TOKEN` locally from upstream `AGENT_REGISTRATION_TOKEN`;
never paste it into chat or command-line arguments. From SwitchCrew:

```sh
mkdir -p .switch
chmod 700 .switch
uv run python scripts/register_agents.py --url http://127.0.0.1:8080 \
  --name 'PM Agent' --description 'Coordinates read-only research' --output .switch/pm.json
uv run python scripts/register_agents.py --url http://127.0.0.1:8080 \
  --name 'Research Agent' --description 'Retrieves configured public evidence' --output .switch/research.json
```

Transfer returned IDs/keys into the matching local settings without printing the
files. The registration script refuses to overwrite keys. Revoke orphaned
identities if registration succeeds but local key persistence fails. Unset the
registration token after provisioning; runtime workers use only their own keys.
