# Engineering → independent QA

Branch base: `2441365` (`codex/switch-live-verification`). This work uses a
separate worktree and never starts, restarts or reconfigures PM/Research.
Publication, Release approvals and voice are outside this implementation.

## Execution model

Engineering consumes the existing durable `AgentRuntime` handoff contract.
`EngineeringExecutor` derives an assignment ID from the handoff ID, records
immutable inputs in a separate SQLite execution ledger, and clones the local
operator-selected source into `<checkout_root>/<assignment_id>`. It checks out
and records the resolved base commit. Existing work is never reset on retry. The new clone locally excludes the
untracked `.claude/settings.local.json` used by Console onboarding and the
completion receipt. A result containing either file in the committed tree is
rejected, preventing those configuration/receipt files from entering the diff.

`ConsoleCodex` talks to the **real local Switch Console control API**. The pinned
source inspected for its contract is:

- `console/apps/switch-console-desktop/src/main/core/control-api/control-service.ts`
- `console/apps/switch-console-desktop/src/main/core/control-api/control-server.ts`
- `console/apps/switch-console-desktop/src/shared/core/sessions/sessions.ts`

These declare authenticated GET agent/list-sessions and POST create-session
routes. The API binds loopback and uses `x-switch-control-token` from Console's
private `control-api.json`; it is distinct from the Switch Core gateway API.
The executor verifies `providerId=codex`, the expected Switch agent name and
`switchAgentId`. Since these control routes expose only `locationId`, it reads
the operator-selected Console SQLite database in read-only mode and verifies the
`locations.dir` and local `ssh_host` against the assignment checkout. The pinned
schema is `console/apps/switch-console-desktop/src/main/db/schema.ts`; no Console
database writes or migrations are performed. The agent must have
no sessions belonging to other assignments. Session title is
`switchcrew:<assignment_id>`; this is the reconciliation key because this API
creates random session IDs and offers no idempotency key.

After session creation, the Engineering runtime sends the implementation
request through its authenticated Switch connection to the dedicated Codex
agent. The managed session must be connected to this test room and report
`live`. The request includes the stable assignment, checkout and acceptance
criteria. It instructs Codex to implement and commit its change and atomically
write an untracked `.switchcrew-result.json` receipt:

```json
{
  "assignment_id": "assignment hash from the handoff",
  "status": "implemented",
  "final_revision": "full actual commit SHA"
}
```

`implementation_failed` is also accepted. Engineering verifies the receipt's
assignment and HEAD, ancestry from the base, and absence of other uncommitted
files. Its result records base revision, final revision, binary-capable Git diff,
checkout and managed session ID. Receipt files are not copied into QA's clone.
An `implemented` result means a committed implementation exists; QA has not yet
accepted it.

Engineering creates a distinct durable handoff to the configured QA identity
using `enqueue_independent_qa`. The QA handoff ID is derived from the Engineering
handoff ID **and final revision**. Reconciliation creates the same handoff after
restart. QA commands are selected from operator configuration, never from the
Engineering prompt, receipt or implementation code.

`QAExecutor` creates a fresh clone and detached checkout at that exact revision
for **each command**. Commands cannot share generated build artifacts or a HOME
directory. Configure each validation command to work independently against the
pinned source. It runs argv arrays without an implicit shell, with an external,
per-command HOME and a minimal
environment that excludes application keys, caller PYTHONPATH and user tokens.
Every executed command records argv, exit code, timeout, tested revision,
bounded output summary, full output SHA-256 and byte count. Command intent and
finished command records are durable. A command that changes tracked code, HEAD,
or leaves new untracked/ignored inputs fails validation before another command
runs. Only Python `__pycache__/*.pyc` and enumerated pytest metadata files are
allowed; even these caches do not carry into the next command's fresh clone.
Arbitrary build products left inside the checkout fail this conservative policy.
Timeouts terminate the process group. Retained QA directories allow inspection.

These are separate Git checkouts and process environments, **not OS security
sandboxes**. Commands can access the host and network. Use a dedicated execution
account/container/VM for untrusted target repositories. A machine loss during QA
can leave a subprocess that needs operator reconciliation before another QA
handoff is issued. No automatic retry is assumed safe for arbitrary commands.
Worktree checks run after each command; they do not detect temporary source
changes created and removed within a command, or make malicious commands safe.
Validation argv is trusted operator configuration.

## Outcomes and reconciliation

| Outcome | Meaning |
| --- | --- |
| `blocked_configuration` | Missing/mismatched managed agent, dirty initial checkout, changed assignment inputs, missing commands, unavailable implementation, or interrupted QA requiring reconciliation. |
| `pending_execution` | Session creation/delivery response is uncertain, or the existing session has not produced its receipt. Inbox/handoff remain incomplete. |
| `implementation_failed` | Managed session reported failure or its receipt/committed state failed verification. |
| `implemented` | Engineering completed a verified committed artifact; separate QA is required. |
| `qa_failed` | A validation command failed/timed out, could not launch, or changed the tested code. |
| `stale_qa` | The implementation ledger or checkout no longer matches the requested QA revision. |
| `verified_success` | All configured validation commands passed on the recorded exact revision; this confers no publication authorization. |

Session creation is claimed durably **before** POST. If its response is lost,
subsequent attempts list sessions and adopt the uniquely matching title. If no
matching session can be found, the assignment remains pending and never issues
another creation POST. Prompt delivery is also claimed before the Switch
mutation; an uncertain send is never repeated. An operator must reconcile an
absent session/receipt before using a new assignment to retry.
Once session creation or delivery has been claimed, a Console validation outage
keeps the assignment pending instead of completing it as blocked. The managed
agent/location identity must validate again before a receipt is accepted. A
non-object or otherwise malformed receipt fails implementation verification.

QA checks implementation currency before running, after running, and before
returning a cached result. A consumer must compare both
`implementation_assignment` and `tested_revision` with the currently selected
Engineering result; an older completed QA handoff must never approve a newer
implementation. Changing either inputs or validation commands requires a new
handoff. A process interruption with a `running` QA record returns blocked with
any durable command evidence instead of rerunning commands blindly.

QA handoff creation checks task liveness in the same SQLite transaction as the
new assignment. Executor completion also rechecks liveness transactionally;
late results for a terminal task become blocked, including a pending execution
returned after cancellation. Unsent handoff messages for terminal tasks are
retained but skipped. These checks cannot recall a network request already in
flight or forcibly stop a running external Codex session/command.

The integration runtime polls pending executions when no new SSE message
arrives. Polling waits until the room claim completes and pauses on transport
disconnect/heartbeat refusal, including the adapter's internal reconnect backoff.
It deliberately uses graphs without checkpoint caching: a pending
external operation must be reconciled on the next invocation. Existing graph
memory hooks still prime/recall/observe curated outcomes. Waggle remains disabled
unless the private integration config explicitly includes
`memory: {"memory_enabled": true, ...}`. Use the existing settings field names
and transports documented in `docs/MEMORY.md`. Shared project identity is
`github.com/Abhigyan-Shekhar/flip`; provenance remains role/task scoped. Raw code,
command output, prompts and recalled memory are not added to memory summaries.

## Standalone integration entry point

Create a **new test room** and independent Engineering, QA, coordinator and
managed Codex identities. Do not reuse the running PM/Research room, keys,
connection IDs, application DB or checkout. Codex needs its provider login and
an operational Console host. Give it access only to the test room; configure its
working directory to the expected assignment checkout. The adapter deliberately
does not change agent settings or bootstrap login credentials.

Each runtime uses a private registry compatible with `runtime.AgentConnection`:
`role`, `agent_id`, transport `participant_id`, exact machine `name`, `api_key`,
`room_id`, `allowed_senders` (transport sender → registration ID), and
`target_names` (registration ID → machine name). Engineering allows the test
coordinator and QA; QA allows Engineering. Engineering targets the coordinator,
QA and the dedicated Codex machine name as appropriate. Switch addressing must
also allow the intended sender identities in this test room.

Engineering config example (save outside tracked files; chmod 600):

```json
{
  "role": "engineering",
  "registry": "/private/integration/engineering.json",
  "test_room_id": "NEW_TEST_ROOM",
  "switch_url": "http://127.0.0.1:8080",
  "database": "/private/integration/application.sqlite3",
  "ledger": "/private/integration/executions.sqlite3",
  "checkout_root": "/private/integration/engineering",
  "control_file": "/path/to/SwitchConsole/control-api.json",
  "console_database": "/path/to/SwitchConsole/switchdash.db",
  "qa_agent_id": "NEW_QA_REGISTRATION_ID",
  "jobs": {
    "ENGINEERING_HANDOFF_ID": {
      "source": "/absolute/local/target-repository",
      "base_revision": "PINNED_BASE_COMMIT",
      "console_agent_id": "DEDICATED_CONSOLE_CODEX_AGENT_ID",
      "codex_switch_agent_id": "DEDICATED_CORE_CODEX_REGISTRATION_ID",
      "codex_name": "engineering-codex-test"
    }
  }
}
```

QA uses the same isolated application DB and execution ledger, its own registry
and role, and a different checkout root. Replace Engineering's `jobs` with:

```json
{
  "reviews": {
    "ENGINEERING_HANDOFF_ID": {
      "commands": [["/absolute/path/to/python", "-m", "pytest", "-q"]],
      "timeout": 120
    }
  }
}
```

QA derives the review handoff ID and exact revision from the completed
Engineering handoff, and reads the corresponding assignment from the shared
execution ledger. Manual pinned QA jobs are also supported via `jobs` keyed by
QA handoff ID with `implementation_assignment`, `revision`, `commands`, `timeout`.

Before sending the Engineering handoff, prepare its checkout and then onboard
the dedicated Console agent at that exact path. Preparation can invoke
`EngineeringExecutor(ledger, checkout_root, source, base_revision, None)` with
the actual handoff state: it creates the clone and returns
`blocked_configuration` plus the checkout path, without storing a final result
or creating a session. Do this directly before starting/delivering to the
runtime; delivering an unconfigured job through the runtime would complete its
handoff as blocked and require a new handoff to retry. Once Console is configured,
the same prepared assignment can execute through the real adapter.

Start the two independent processes:

```sh
uv run python -m switchcrew.engineering_qa --config /private/integration/engineering-config.json
uv run python -m switchcrew.engineering_qa --config /private/integration/qa-config.json
```

The Switch origin uses the established HTTPS/loopback validation. CLI failures
log only allowlisted diagnostics. A lost-ownership exception, including one
wrapped by a TaskGroup, exits 0 so an `on-failure` supervisor stands down;
ordinary failures exit 1. The generic specialist CLI uses the same policy.
Do not use an always-restarting supervisor for these identities. The PM/Research
Compose override is documented in `deploy/switch/RECOVERY_ACCEPTANCE.md`; it does
not provision containers for the standalone Engineering/QA processes.

Seed a task and Engineering handoff using the existing `Store.create_task` and
`Store.create_handoff` APIs in the **integration DB**, with the new coordinator
as sender and new Engineering registration as recipient. Use a stable explicit
handoff ID matching the config. The coordinator must deliver that outbox through
its own authenticated runtime; creating a database row alone is not delivery.
Engineering's runtime creates and sends the QA handoff after implementation;
QA's independent runtime acknowledges and completes it through Switch.

### Cross-boundary request to the PM/Research owner

No reserved file was edited. The concurrent owner was not identifiable among
this app's chat inventory, so this is the concrete coordination request:

1. Add optional routing of an approved Engineering task to the standalone
   Engineering identity using the existing durable handoff schema. Provide the
   handoff ID to the operator's job config, rather than passing shell commands.
2. Preserve `pending_execution` as incomplete work when reconciling external
   executors. This branch adds only that minimal handling in owned `runtime.py`.
3. Observe Engineering and QA acknowledgments independently. Require the QA
   result's implementation assignment and exact tested revision to match the
   currently selected Engineering result before reporting verified success.
4. Coordinate the additive `graphs.RESULT_STATUSES` entries when integrating
   parallel graph changes. Do not merge this branch over the owner's workers.

## Evidence: controlled results versus live gates

**Controlled tests:** `tests/test_engineering_qa.py` uses actual temporary Git
repositories, actual commits, separate QA clones and real subprocess commands.
A source containing `broken` fails a command asserting `correct`; a separate
corrected Engineering assignment passes. Each result contains the exact tested
SHA and output/exit evidence. Tests exercise lost creation and delivery responses
with reopened SQLite state, an unknown creation that never relaunches, interrupted
QA, stale cached QA rejection, invalid receipts, changed assignment inputs,
validation modifying code, timeouts, graph contracts, durable one-time QA
handoffs and keeping runtime inboxes pending. Controlled session doubles write
fixture commits; **they are not evidence of Codex integration**.

**Live prerequisite check, November 1, 2026:** the running local Console control
API returned HTTP 200 for GET `/agents` with `agents: []`. Its token was read
privately and never printed. There is no registered Console-managed Codex agent
available to validate/start. No new live room or identities were provisioned,
and no PM/Research deployment changes were made.

**Remaining live gate:** provision the dedicated test room and identities above,
register/login a real Console Codex provider at the assignment checkout, then
run a small actual implementation through the standalone Engineering runtime.
Record its managed session ID, title, delivery event, base/final SHA and diff.
Run independent QA once on a deliberately broken implementation and once after
a corrected assignment, then interrupt session creation/delivery and reconcile
by title/receipt; change the implementation revision and demonstrate stale QA
rejection. Capture actual task/handoff/message IDs separately from controlled
test evidence. This branch does not claim that live acceptance has passed.

## Branch validation

Initial Person 2 validation in the separate `engineering-qa` worktree: **136 full-suite tests
passed**, including 14 Engineering/QA cases. `ruff check .`,
`ruff format --check src tests scripts`, and `git diff --check` passed. The
standalone module's `--help` entry point was also exercised. No reserved
PM/Research, deployment, README or operational-status file was changed.

During integration with recovery hardening, independent GPT-6.1 Terra reviews
identified and fixed cross-command QA contamination, terminal-task assignment
and result races, a post-delivery Console-outage reconciliation failure, and
malformed receipt shapes. Parent integration added connection-readiness gates,
disconnect lifecycle callbacks, secure origin validation and takeover-aware
CLI exit handling. The combined checks and live limitations are recorded in
`docs/DAY1_STATUS.md`; controlled Git/session doubles remain separate from real
Console Codex evidence.
