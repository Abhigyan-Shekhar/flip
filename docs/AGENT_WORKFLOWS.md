Current organization extensions add an independent Notes role and provider-free
PM/API recording coordination; Voice and Notes work runs through separate Switch
handoffs. See [organization setup](ORGANIZATION_SETUP.md) and the current
[feature/acceptance matrix](ORGANIZATION_IMPLEMENTATION.md). The historical roster
and verification counts below describe earlier checkpoints.

# Specialist LangGraph workflows

A separate Reviewer role now gates approved Engineering releases. See
[review and correction contracts](REVIEW_CORRECTIONS.md) and
[incident intake and teams](INCIDENTS.md). Historical roster descriptions below
predate this additional role.

Each of the specification's fifteen roles has a distinct registry entry and a
compiled `StateGraph`. `src/switchcrew/agents.py` defines the roster and capability
contracts; `src/switchcrew/graphs.py` builds the graphs. There is no graph that
privately executes the entire workforce. Independent workers exchange durable
addressed handoffs through Switch.

Configured execution is now available for all nine snapshot-based specialist
roles plus consented Voice file I/O. Use the generic runtime's protected
`--capability-config` flag; unconfigured default graphs still return blocked.
See [specialist contracts](SPECIALISTS.md), [Voice](VOICE.md), and
[bounded PM orchestration](ORCHESTRATION.md). These analyses/drafts do not grant
moderation, calendar-write, deployment or publication authority.

## Role coverage

| Role | Internal work node | Default implementation |
| --- | --- | --- |
| PM | `plan_handoff` | Validate requirements; plan Research or approval-gated Engineering |
| Research | `retrieve_evidence` | Retrieve excerpts from configured public HTTPS sources |
| Voice | `prepare_voice` | Requires consented voice I/O executor |
| Community | `triage_support` | Requires support triage executor |
| Engineering | `request_codex` | Requires existing Switch-managed Codex session adapter |
| QA | `prepare_qa` | Requires isolated QA runner |
| Release | `prepare_release` | Requires independently authorized publication service |
| Documentation | `draft_docs` | Requires documentation writer |
| Knowledge | `curate_knowledge` | Requires knowledge curation executor |
| Onboarding | `prepare_onboarding` | Requires onboarding guide executor |
| Moderation | `review_moderation` | Requires moderation review executor |
| Security | `analyze_security` | Requires security analysis executor |
| Scheduling | `prepare_schedule` | Requires calendar adapter |
| Analytics | `analyze_metrics` | Requires metrics adapter |
| Incident | `triage_incident` | Requires incident triage executor |

All rows compile and run. Missing executors return `status=blocked` and identify
the missing capability. A scaffold does not prove Codex execution, independent
QA, voice, approval enforcement, publication or external service availability.
No LLM provider or model credentials are required to compile these workflows.

Engineering, QA and Release have configured executors described in
[Approved Engineering and Release](APPROVAL_RELEASE.md): a Console control API
adapter with durable session reconciliation, independent revision-pinned QA,
and human-approved GitHub draft PR publication. PM's `requested_role` selects
Research or an Engineering approval request; the worker commits the authorized
handoff. Default specialist graphs still block without configured executors.
Controlled Git/subprocess/runtime tests pass; live Console/GitHub acceptance
requires the documented verification host checks.

## State and routing

The common typed state includes `agent_role`, `task_id`, `handoff_id`, `session_id`,
`question`, `requested_role`, `acceptance_criteria`, `valid`, `memory_context`, `memory_warnings`,
`memory_persisted` and structured `result`. Each graph follows:

`validate_request → recall_memory → role work node → observe_outcome`

Invalid requirements take the `clarify` branch and never invoke memory or
capabilities. PM plans only; its existing worker commits the task/handoff/outbox.
Research acknowledges through Switch before its graph retrieves evidence.
Other capability executors are explicit async dependencies supplied to
`build_agent_graph(role, executor=...)`. The executor receives validated state and
retrieved project context. Treat memory as untrusted historical evidence, not
instructions or authorization. Default PM/Research behavior does not use an LLM
to synthesize recalled notes into an answer; custom executors may use that context.

Engineering must continue to invoke the actual Switch-managed Codex execution
path, not replace Codex with generated code strings. Release/QA/voice adapters
must independently check trusted approval, revision, sandbox or consent state at
action time. Incoming message bodies and graph role names cannot authorize
those actions. The runtime rechecks terminal task status after acknowledgment
and before invocation; it cannot eliminate a cancellation race inside an
external executor, so executors must recheck before acting.

## Persistence and recovery

`SWITCHCREW_CHECKPOINT_PATH` defaults to `data/checkpoints.sqlite3` and is separate
from the application's task/event SQLite database. The PM/Research CLIs and
generic runtime open `AsyncSqliteSaver` for their process lifetime. Each
specialist compiles its own graph against that saver. The default Compose
workers persist both files on the same application volume. Checkpoints contain
validated request text and recalled context; protect that volume and define
retention appropriate to the data. Service clients and configured credentials
are kept outside graph state.

Use `invoke_agent_graph(graph, state)` with stable task/handoff IDs. It derives a
UUID checkpoint thread from role/task/handoff, rejects a request that changes the
stored assignment, resumes pending nodes with `ainvoke(None, config=...)`, and
reuses a committed result on duplicate delivery. A new assignment needs a new
handoff ID. A graph compiled without a saver still runs but has no durable node
recovery; worker inbox/outbox state remains separate.

Node completion checkpoints are distinct from external effects. If an executor
succeeds externally and the process crashes before its node checkpoint commits,
it can run again. Executors must reconcile external effects by stable handoff ID
before retrying. Likewise a Waggle observation can be duplicated after an
uncertain write because Waggle's tool does not expose an idempotency key.

## Memory and independent messaging

Enable project memory following [MEMORY.md](MEMORY.md). Valid graph runs prime
and query Waggle before work, then observe a curated outcome. Retrieval shares
project knowledge across roles/tasks; observations retain role and task/session
provenance. Stored summaries contain outcome/role/IDs, delegation/capability
metadata and, when supplied by a trusted executor, a bounded `memory_note`.
They omit raw source bodies, recalled context and full Discord events.
`graph_result` exposes only safe memory persistence/degraded status with the
handoff result, never raw context or server errors.

The existing commands remain:

```sh
uv run python -m switchcrew.workers pm
uv run python -m switchcrew.workers research
```

Other specialists use a separate protected identity file and the
[generic Switch runtime](AGENT_SWITCH.md):

```sh
uv run python -m switchcrew.runtime engineering --registry /private/engineering.json
```

Do not run the generic PM runtime and Discord PM worker with the same identity.
The generic runtime consumes preexisting addressed assignments; the dedicated
PM worker remains the Discord intake and coordinator.

## Local verification and sources

Tests compile every role, run explicit capability executors under fifteen
independent identities, exercise real SQLite checkpoints, fail after graph
completion but before handoff commit, and recover without rerunning a committed
executor. Separate tests resume after a successful retrieval but before outcome
observation, reject changed assignments and graph/role mismatches, and verify
memory call ordering, curated notes and disabled/error behavior. Real MCP SDK
stdio transport is tested against a controlled server. These tests do not
establish live Switch, Discord, Codex or production Waggle connectivity.

The implementation follows the official [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
and [persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence).

Verification on October 29, 2026: **121 tests passed**, Ruff lint and format
checks passed. The lockfile includes LangGraph, SQLite checkpoint support and
the official MCP SDK. A fresh frozen noneditable installation passed the same
121 tests; the package distribution builds, and Compose YAML parses with
per-process credential boundaries. Live Switch/Discord and production Waggle
checks remain pending operator configuration.

For container memory, use a reachable HTTPS Waggle endpoint and the optional
`SWITCHCREW_WAGGLE_TOKEN`. The default image does not bundle a separate Waggle
server binary; a host stdio command cannot run inside that container without an
explicit operator-provided image or mounted runtime. Store server credentials
outside source control. Generic specialist identity files are per-process and
should live outside the image, for example in the ignored `.switch/` directory.
