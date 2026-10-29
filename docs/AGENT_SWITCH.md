# Independent specialist runtimes

`python -m switchcrew.runtime ROLE --registry /private/ROLE.json` runs one
specialist graph behind its own authenticated Switch protocol 6 connection.
Supported roles are `voice`, `community`, `pm`, `research`, `engineering`, `qa`,
`release`, `documentation`, `knowledge`, `onboarding`, `moderation`, `security`,
`scheduling`, `analytics`, and `incident`.

Each process uses one current-agent secret file (mode 0600 recommended). Peers
appear only as public identities. For example, the engineering process reads:

```json
{
  "role": "engineering",
  "agent_id": "registered-engineering-agent-id",
  "participant_id": "registered-engineering-participant-id",
  "name": "Engineering Agent",
  "api_key": "CURRENT_AGENT_KEY_ONLY",
  "room_id": "registered-team-room-id",
  "allowed_senders": {
    "registered-pm-participant-id": "registered-pm-agent-id"
  },
  "target_names": {
    "registered-pm-agent-id": "PM Agent"
  }
}
```

Agent IDs identify durable assignments; participant IDs identify authenticated
Switch message senders; names identify Switch targeted-message destinations.
Use separate registrations and keys for each specialist. Do not copy PM's key
into specialist files. `allowed_senders` is an explicit inbound allowlist;
`target_names` is the outbound public address book. Register identities using
`scripts/register_agents.py` and provision their room access before starting
processes. The runtime never registers agents or starts infrastructure itself.

Start each role with its own registry; all runtimes in this local deployment use
the same application SQLite store and configured checkpoint store:

```sh
python -m switchcrew.runtime engineering --registry /private/engineering.json
python -m switchcrew.runtime qa --registry /private/qa.json
```

The default URL and database come from `Settings` (`SWITCHCREW_` environment
variables / `.env`). `--url` and `--database` override them. URL overrides pass the
same HTTPS/origin validation as the primary workers. `--registry` may also come
from `SWITCHCREW_AGENT_REGISTRY`. `checkpoint_path` configures a separate LangGraph
SQLite checkpoint file. Memory uses the configured Waggle adapter when enabled;
no credentials or clients enter checkpointed graph state.

For all fifteen processes, prepare one registry for every role, then run each
command in its own process supervisor entry:

```sh
for role in voice community pm research engineering qa release documentation \
  knowledge onboarding moderation security scheduling analytics incident; do
  python -m switchcrew.runtime "$role" --registry "/private/$role.json" &
done
wait
```

The existing `switchcrew.workers pm` and `switchcrew.workers research` commands
remain the Discord-to-PM-to-Research product flow. Choose one process for a given
registered identity: the generic runtime accepts existing addressed handoffs and
does not replace Discord intake or the PM task planner. Running two processes
for one identity deliberately triggers Switch's takeover protection.

## Delivery and execution contract

A sender first commits a task and `Store.create_handoff` assignment in the shared
store. Its outbox sends a fenced `switchcrew` JSON envelope containing `type`,
`task_id`, `handoff_id`, and a persisted `delivery_id`. Receipt verifies the room,
authenticated participant-to-agent mapping, task correlation, and the addressed
assignment before committing the inbox. Platform/proxied messages are rejected.
The trusted durable assignment supplies graph inputs; incoming messages cannot
replace its question or acceptance criteria.

The recipient persists acceptance and delivers `handoff.accepted` before invoking
its local graph. It persists a structured result and delivers `handoff.completed`
back to the original sender. A blocked capability is a blocked result, never a
claim that the requested work completed. The default CLI provides read-only
Research; other capability executors must be explicitly injected via
`build_agent_graph(..., executor=...)` and `AgentRuntime(..., graph=graph)`.
PM's generic graph returns a delegation plan; only an authorized coordinator
commits subsequent assignments. No runtime directly calls another role's graph.

Stable inbox/outbox IDs suppress transport replay, and each identity persists
its SSE cursor. History recovery reuses the same validation and deduplication.
LangGraph checkpoints use stable role/task/handoff keys to resume pending nodes
or reuse a committed result after restart. External executors must themselves
honor `handoff_id` as an idempotency key: a crash during an external operation can
repeat that operation before its graph checkpoint commits. A failed or ambiguous
POST stops processing, retaining durable pending delivery for supervisor restart.
This scaffold does not authorize publication, deployment, or autonomous changes.

`tests/test_runtime.py` exercises real SQLite delivery state and all fifteen
compiled graphs with explicit test executors, plus replay, room/sender/task
filtering, history recovery, and acknowledgment delivery failure. These local
checks require no live Switch credentials or deployment.

Generic runtimes consume peer acknowledgment/result envelopes only after checking
their sender against the original assignment. These receipts complete their
inbox entries; durable acceptance/result facts already live in the shared
handoff and event records. They do not automatically schedule another specialist
or change the parent task's status. A coordinator must explicitly consume those
records and decide the next authorized assignment.

Before every initial or resumed graph invocation, the runtime rereads the parent
task after acknowledgment delivery. A `CANCELLED` or `COMPLETED` task produces a
blocked handoff with reason `task_terminal` without running the graph. Executors
must also check authorization and task state at the moment of any external
action, since cancellation can race with ongoing execution. Executed handoff
results include bounded memory diagnostics (`persisted` plus a static status); raw recalled memory and adapter error text are not delivered.
