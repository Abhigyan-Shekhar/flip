# Flip Control Room and native Discord slash commands

The read-only operator interface is served by the **existing FastAPI service**:

- `GET /dashboard` — self-contained dashboard (no build pipeline or CDN).
- `GET /operator/snapshot` — bearer-protected, bounded tasks, dependency
  steps, configured agents, approvals, recent activity and failures.
- `GET /tasks/{id}/trace` — existing authenticated sanitized execution trace,
  loaded when an operator selects a task.

The dashboard intentionally has **no ability to approve or execute** tasks.
Enter the existing private API bearer token in the UI. It is held only in
that browser tab's memory, cleared from the field after connecting, and never
stored in cookies or localStorage. The UI uses text-only DOM assignments for
untrusted fields. All task/trace data still comes from authenticated APIs;
the public HTML shell contains no credentials. Serve behind local-only or
authenticated HTTPS infrastructure on a real host. Agents marked `Configured`
are not claimed to be live online.

## Native slash command provisioning

`POST /discord/interactions` validates Discord's Ed25519 signature over the
exact timestamp and raw body, rejects stale timestamps and requests over 32 KiB,
and enforces existing guild/user/room bindings. Scoped `/crew` inspection and
`/agent-connections` return private responses within Discord's message limit.
`/flip` approval and merge commands use separate workflow authorization.

1. In the Discord developer application, configure the interaction endpoint
   as the externally reachable HTTPS `/discord/interactions` URL.
2. Set the app's **public verification key** in the private `.env`:
   `SWITCHCREW_DISCORD_APPLICATION_PUBLIC_KEY=<64 hex chars>`. Do not put
   your bot token, client secret or application private key in this setting.
3. Provision the allowed guild IDs, human user IDs and exact verified Switch
   bridge bindings using the existing private settings.
4. Register the command JSON returned by
   `uv run python -c 'import json; from switchcrew.discord_interactions import slash_manifest; print(json.dumps(slash_manifest(), indent=2))'`
   through the operator-owned Discord registration process. This repository
   does not push command definitions to Discord automatically.
5. Validate signed PING, authorized read-only task lookup in its original
   thread, replay/invalid signature rejection, and denial across guilds or
   threads. The HTTP endpoint and parsing have offline tests. Temporary registration and
   a real signed status request passed on November 4 after the thread-mapping
   fix; routing was restored and the temporary command removed. See the
   [verification record](VERIFICATION_2026-11-04.md). Persistent hosting and
   comprehensive live authorization/replay coverage remain pending.

### Inspect an agent's MCP connections

Register `/agent-connections` from the same manifest. Set
`SWITCHCREW_DISCORD_CONNECTION_VIEWERS` to a JSON object mapping `pm` and/or
`research` to the Discord user IDs that may view each agent's grants. The user
must also have an exact Discord binding to `SWITCHCREW_SWITCH_ROOM_ID`. An empty
object disables access.

The command uses the selected agent's configured ID and API key to read its
grants from Switch. Enable `service_connections` in the Switch server's
`FEATURE_FLAGS_ENABLED` and configure the agent's grants in Console. The
private reply shows MCP service names and the Console Connections URI. In
clients that don't open the URI, navigate to **Connections** in Switch Console.

### Publish a website preview from Discord

1. Set `SWITCHCREW_VERCEL_TOKEN` in the API service's private environment.
   Set `SWITCHCREW_VERCEL_PREVIEW_PROJECT` to a dedicated static demo project
   name. Vercel can create the project on the first deployment. For a team
   project, also set `SWITCHCREW_VERCEL_TEAM_ID`.
2. Add the authorized publisher's Discord user ID to the JSON array
   `SWITCHCREW_DISCORD_SITE_PUBLISHERS`. The user also needs a verified
   binding to `SWITCHCREW_SWITCH_ROOM_ID`. Register `/launch-site` from the
   same slash manifest.
3. In the bound Discord channel or mapped thread, run
   `/launch-site title:Night Shift Coffee description:A small coffee bar for late nights.`
   The command publishes the supplied text in a static page and returns the
   preview URL privately when Vercel reports it ready. The URL follows the
   Vercel project's deployment protection settings.

The Discord invocation authorizes publishing the supplied text. Use demo copy
intended for a website. The workflow creates a Vercel preview and does not
promote it. If the API restarts during publication or reports an uncertain
result, check the Vercel dashboard before submitting a fresh command.

For prompt-based builds, configure an authenticated `pm` CLI harness through
`SWITCHCREW_HARNESS_CONFIG` on the API host and register `/build-site` from
the manifest. The API process must be able to run the configured CLI. Use
`/build-site prompt:Build a late-night coffee bar site with hours and a menu`.
After opening the preview, use `/build-site` with a new prompt and the returned
`revision` ID. SwitchCrew passes the saved HTML to the model and publishes a
new preview. Revisions are available to their author in the bound Switch room.
The generated site is a single static HTML page. Its Vercel content security
policy blocks scripts, form submissions, and external assets.

### Verified thread identifiers

Discord sends a thread's channel ID for commands invoked inside it. Switch
stores its own opaque thread-root message ID. Keep the human identity binding
on the parent Discord channel, and configure a separate operator-verified
mapping in `SWITCHCREW_DISCORD_THREAD_BINDINGS`:

```json
[
  {
    "guild_id": "DISCORD_GUILD_ID",
    "channel_id": "DISCORD_PARENT_CHANNEL_ID",
    "thread_id": "DISCORD_THREAD_CHANNEL_ID",
    "room_id": "SWITCH_ROOM_ID",
    "switch_thread_id": "SWITCH_ROOT_MESSAGE_ID"
  }
]
```

Resolve these values from the actual Discord thread and Switch bridge message
records, not from request text or the task ID supplied to a command. The signed
Discord channel's ID and parent must match the mapping, and the mapped room must
match the authenticated user's existing binding. Missing, ambiguous or mismatched
mappings fail closed. A mapping never authorizes access to a different task
thread. Add mappings for new threads before enabling inspection there.

An application's HTTP interactions endpoint also takes over delivery of its
other slash commands. When testing with the same bot as Switch's built-in
commands, restore its original endpoint and remove the temporary `/crew`
registration afterward. A persistent deployment needs a separate application
or a dispatcher that handles every command offered by that application.

## Code-only verification

Before live Discord provisioning, run the reproducible offline gates on the
final merged checkout:

```bash
uv sync --frozen
uv run --no-sync ruff check .
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync pytest -q
uv build
```

These checks validate signature handling, scope isolation, bounded responses,
and dashboard API behavior; they do **not** establish that a Discord command
is registered, a service is deployed, or an agent is online. Keep those
acceptance outcomes pending until tested on the verification host.

## Known operational limits

- The snapshot lists at most 100 tasks, 100 activity events and 100 workflows;
  it is not a substitute for a historical analytics store.
- There is no active websocket/push refresh. Operators reconnect to load a
  snapshot. Live agent presence must be observed from Switch and separately
  implemented; this dashboard does not fabricate connectivity status.
- Provisioning remains operator-controlled. Separate worker registrations,
  Switch namespaces, Discord permissions and host-scoped credentials are
  required before the dashboard can display a true deployed crew.

## Integration verification sequence

After this final stack layer is merged, use the same verification host for the
preflight report (`docs/DEPLOYMENT_ACCEPTANCE.md`), recorded-meeting gate
(`docs/RECORDED_MEETING_ACCEPTANCE.md`), and memory/graph gate
(`docs/COMBINED_MEMORY_ACCEPTANCE.md`). Review the operator snapshot and
Discord scoped read-only responses against those independent receipts. A green
CI run or a populated dashboard alone must not be labeled an end-to-end
production deployment.
