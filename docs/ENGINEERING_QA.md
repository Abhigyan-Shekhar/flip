# Engineering → independent QA

The mandatory independent Reviewer and bounded correction loop are documented in
[REVIEW_CORRECTIONS.md](REVIEW_CORRECTIONS.md). Reviewer identity/configuration is
now required; older workflow diagrams below describe the pre-review lifecycle.

Current deployment instructions are in [Approved Engineering and Release](APPROVAL_RELEASE.md).
The runtime now requires a durable human-approved PM workflow for Engineering
and QA, and also supports Release. Legacy manually seeded Engineering handoffs
and `jobs`/`reviews` configuration no longer authorize runtime execution. Use the
new runbook's shared database, approval flow and `job_templates`.

Original branch base: `2441365` (`codex/switch-live-verification`).

## Execution model

Engineering consumes the existing durable `AgentRuntime` handoff contract.
`EngineeringExecutor` derives an assignment ID from the handoff ID, records
immutable inputs in a separate SQLite execution ledger, and clones the local
operator-selected source into `<checkout_root>/<assignment_id>`. It checks out
and records the resolved base commit. Existing work is never reset on retry. The new clone locally excludes the
untracked `.claude/settings.local.json` and `.switch/config/` used by Console onboarding and the
completion receipt. A result containing those configuration files or the receipt in the committed tree is
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

## Approved integration entry point

Use the shared-room, shared-application-database instructions and private
per-worker `job_templates` configurations in
[Approved Engineering and Release](APPROVAL_RELEASE.md). Start each independent
process with `python -m switchcrew.engineering_qa --config /private/worker.json`.
The entry point supports `engineering`, `qa` and `release`; it requires a durable
human-approved workflow. A manually created handoff is insufficient.

Each runtime still reads only its own protected registry and capability config.
Application state and the execution ledger must use separate files. Origins
receive the established HTTPS/loopback validation. CLI failures log only
allowlisted diagnostics; a lost-ownership exception exits zero so an
`on-failure` supervisor stands down. There is no always-restarting takeover loop.

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
