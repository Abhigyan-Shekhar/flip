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
and enforces existing guild/user/room bindings. It only exposes read-only
scoped `/crew` subcommands: `status`, `team`, `why`, `dependencies`, `trace`,
`evidence`. Responses are ephemeral and clipped to Discord's message limit.
There is no native approval/cancel command. This avoids bypassing the
independent human approval workflow.

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
   threads. The HTTP endpoint and parsing have offline tests; registration
   and actual Discord acceptance are **not yet verified**.

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
