# Local operation

[Approved Engineering and Release](APPROVAL_RELEASE.md) connects PM requests to
Engineering, independent QA and GitHub draft PR publication through two durable
human approval stages. It documents shared state, private worker templates,
identity/credential boundaries and the live acceptance runbook. The underlying
Console and QA contracts are described in [Engineering/QA](ENGINEERING_QA.md).
Real Console/GitHub acceptance remains pending on the verification host.

The PM/Research workers now execute independent LangGraph workflows with durable
SQLite checkpoints. See [all agent workflows](AGENT_WORKFLOWS.md), optional
[Waggle configuration](MEMORY.md), and [additional Switch runtimes](AGENT_SWITCH.md).

Use CPython 3.12 and `uv sync --frozen`. The lockfile includes runtime and test
versions. Start the API with `uv run uvicorn switchcrew.api:create_app --factory
--host 127.0.0.1 --no-access-log`. On startup the service creates the SQLite
schema and rejects a database from an unsupported future schema version.

Configuration is read from environment variables prefixed `SWITCHCREW_` and the
local `.env` file. `.env.example` documents the fields. `API_TOKEN` must contain
at least 32 random characters; startup fails without it. `DATABASE_PATH` defaults
to `data/switchcrew.sqlite3`. Empty Discord allowlists deny all human requests.
Lists and bindings use JSON syntax, not comma-separated strings. Keep the file
mode 0600 and store only real credentials locally. Exceptions and validation
responses omit request values; API logs record request IDs, methods and status.

## API routes

| Route | Authentication | Behavior |
| --- | --- | --- |
| `GET /health` | Public | Process/database availability |
| `GET /openapi.json` | Bearer | Machine-readable request contracts |
| `POST /tasks` | Bearer | Create task and first event atomically |
| `GET /tasks/{id}` | Bearer | Task, source context, state and version |
| `GET /tasks/{id}/events` | Bearer | Ordered durable event history |
| `POST /tasks/{id}/transitions` | Bearer | Checked research state change or pre-publication cancellation |
| `GET /tasks/{id}/workflow` | Service bearer | Engineering workflow, approvals, QA and receipt |
| `POST /tasks/{id}/approvals` | Human bearer | Version-checked Engineering/release approval or rejection |
| `GET /tasks/{id}/plan` | Service bearer | Persisted orchestration steps, child IDs and results |

Task creation takes `{"objective":"Research Switch reconnect behavior",
"acceptance_criteria":["Find source excerpts"],"source":{"room_id":"...",
"thread_id":"..."}}`. The returned initial version is 0. A transition takes
`{"status":"PLANNED","expected_version":0}`. Stale or illegal changes return
409, missing tasks 404, missing/wrong credentials 401 and invalid bodies 422.
Generic transitions retain the research state allowlist; approved Engineering
state advances only through the dedicated workflow. Human credentials are
independent of the service API token; approval authentication failures return 403.
API-created research tasks are stored only. Explicit engineering requests with
`kind: "engineering"` and a configured `job_key` create an approval request;
after human approval PM delivers the durable handoff to Engineering. See the
[new runbook](APPROVAL_RELEASE.md) for bodies, setup and authorization limits.
Voice endpoints remain outside this implementation.

`kind: "orchestrate"` creates a bounded dependent plan from 1–12 criteria. PM
routes only to configured specialist identities, polls durable completions and
requires separate approvals for every Engineering child. See
[orchestration setup](ORCHESTRATION.md). Generic configured specialists launch
with `--capability-config /private/role.json`; the protected file selects local
snapshot analysis or consented Voice I/O. It cannot replace the privileged
Engineering/QA/Release entry point.

## Independent workers

Provision Switch and Discord using [the guide](../deploy/switch/README.md).
Register distinct always-on PM and Research workers, keep each returned API key,
then set each registration ID, verified room participant ID, exact participant
name, room ID and bridge URL in `.env`. Do not conflate these three identity forms.
Use machine names `pm-agent` and `research-agent` for targeting. Membership UUIDs
are not necessarily the transport sender IDs; verify senders against authenticated
history and the stored external-user/client mapping as described in provisioning.
Run exactly one instance per specialist:

```sh
uv run python -m switchcrew.workers research
uv run python -m switchcrew.workers pm
```

Use the same `DATABASE_PATH` for API and both workers. This Day 1 implementation
shares SQLite through separate processes; do not put the database on network
storage. Workers use deterministic connection IDs and persisted stream cursors,
inbox/outbox, tasks, handoffs and results. Docker injects only the relevant agent
key into each worker. Local `.env` is a trusted operator configuration containing
both keys; separate environment-only process launches can further isolate them.

Configure PM's `DISCORD_BINDINGS` with operator-verified mappings, and nonempty
`DISCORD_GUILD_IDS` and `DISCORD_USER_IDS`. The event body cannot assert identity.
Stale bindings must be revoked by the operator when access changes. Configure
Research's `RESEARCH_SOURCE_URLS` to public HTTPS source pages; at most
`RESEARCH_MAX_SOURCES` (1–10, default 5) are retrieved. Connections pin validated
public DNS addresses, reject redirects, cap responses and enforce timeouts.
Results contain source URLs and excerpts; relevance matching is lexical.

Create the test thread on a human message already bridged into Switch. A standalone
bot-created thread can lack an ingested root on this pinned server, causing replies
to land in the parent channel. Verify the actual Discord location of each reply.
For the bounded official source
`https://docs.discord.com/developers/events/gateway.md`, send:

```json
{"question":"What are Discord Gateway intents?","acceptance_criteria":["Find official source excerpts explaining intents"]}
```

PM creates a task, sends a targeted handoff and waits for an explicit Research
acknowledgment. Research sends its acknowledgment before fetching sources, then
returns evidence or a blocked result. PM posts progress and the result in the
same thread; both worker identities appear through Switch's built-in webhooks.
A plain text request prompts for a new JSON request; the initial task remains
`NEEDS_CLARIFICATION`. Excerpts are not a claim that acceptance criteria passed.

## Recovery and limitations

SQLite transactions couple changes with events and outgoing messages. Delivery
is at-least-once: if Switch accepts a send but its response is lost, a later
retry can create duplicate visible Discord messages. Receiver deduplication and
stored handoff results prevent repeated completed research. In-flight read-only
retrieval can repeat after a process crash before its result commit.

A disconnected target retains pending output and causes worker failure so the
process supervisor can retry after reconnect. Outgoing transport acceptance is
separate from application acknowledgment. Run workers under Compose or another
supervisor. Inspect task events and pending work before resetting connection
state. Never delete the database to fix delivery problems.

For Compose workers, include `-f deploy/switch/worker-recovery.yaml` after the
base file and any networking override. Workers exit 0 after an eviction or
`taken_over` refusal and log `worker_ownership_lost`; this override's `on-failure`
policy leaves that holder stopped. The base `unless-stopped` policy would restart
even a clean exit and could steal the winning connection back. Ordinary failures
exit 1 and restart. With `on-failure`, explicitly start workers after a Docker
daemon/host restart. For a host process supervisor, apply the same exit policy.
See [Docker restart semantics](https://docs.docker.com/engine/containers/start-containers-automatically/).

Within a process, reconnects carry `expected_generation` alongside the stable
connection ID and persisted cursor. A `heartbeat_lapsed` frame or recoverable
heartbeat HTTP refusal reopens the stream; `taken_over` remains terminal.
`stream_reconnecting`, `worker_connected`, `worker_role_assumed`,
`history_gap_received` and `history_gap_replayed` expose only allowlisted metadata.
History replay means durable inbox insertion; application processing follows.

SSE history gaps replay specialist messages from trusted sender metadata and
human messages only when the persisted room/sender has one operator-verified
binding. The normal allowlists still apply. Conflicting or missing bindings
cannot authorize replay. Run the live gap/restart acceptance before treating
this as a proven production recovery path.

## Live acceptance procedure

This is a manual live gate, not part of the deterministic test suite:

1. Deploy the pinned Switch stack; verify its health and Console connection.
2. Configure a test guild, bot intents, room, human owner link, verified bindings,
   two specialist identities, room roles and addressing policy.
3. Start both workers. Confirm registration, operation discovery and connections.
4. Submit the JSON request above in a Discord thread. Capture original request,
   task ID, handoff ID, actual Switch message IDs, Research acknowledgment, source
   evidence and PM result in that same thread. Verify both identities are visible.
5. Restart workers/API and fetch task/events to prove persistence. Interrupt
   Research before completion; confirm recovery and no rerun of completed work.
6. Disconnect Research, submit another request and confirm pending delivery is
   retained. Reconnect; confirm acknowledgment and result arrive.
7. Expire a heartbeat and interrupt SSE; confirm reconnect with stable connection
   ID/cursor. Force an SSE history gap and inspect specialist and bound-human replay; reconcile
   any requests without a verified identity binding.
8. Record actual service versions, timestamp, commands and evidence in
   `docs/DAY1_STATUS.md` before marking the live gates complete.

The November 1 verification Mac recorded the real original-thread workflow,
worker/API restarts, disconnected-worker recovery and an injected replay-ticket
gap. Those services and private credentials do not transfer with this repository.
See the [deployment record](../deploy/switch/VERIFICATION.md). Request-during-gap,
explicit heartbeat cases and fresh provisioning remain live gates; the
[recovery runbook](../deploy/switch/RECOVERY_ACCEPTANCE.md) supplies opt-in tests
and a reversible application-cursor injection procedure.
