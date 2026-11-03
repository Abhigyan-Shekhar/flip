# Approved Engineering and Release

The mandatory independent Reviewer and bounded correction loop are documented in
[REVIEW_CORRECTIONS.md](REVIEW_CORRECTIONS.md). Reviewer identity/configuration is
now required; older workflow diagrams below describe the pre-review lifecycle.

The PM worker accepts an explicit engineering request, records it as
`AWAITING_APPROVAL`, and waits for an authorized human. Approval atomically records
the authenticated identity, timestamp and version and creates the PM → Engineering
handoff. Engineering still runs through the existing real Console Codex adapter.
A committed implementation creates an independent Engineering → QA handoff.
Successful exact-revision QA moves the task to `HUMAN_REVIEW`. A second human
approval authorizes Release to push that exact commit and create a **draft** PR.

Either approval can be rejected; rejection cancels the task. Unapproved, forged,
wrong-thread and stale-version decisions cannot start privileged work. Failed,
missing, mismatched or stale QA cannot publish. Generic API transitions cannot
advance this lifecycle. There is no automatic merge or conversion to a ready PR.

## Configure the shared workflow

Use the same application SQLite database for API, PM, Engineering, QA and Release,
and the same separate execution ledger for Engineering, QA and Release. SQLite
files must be on a local filesystem visible to those processes. A host process
cannot use a container's internal `/app/data` path directly: use a deliberate
bind mount or run the complete acceptance workflow natively in a fresh test room.
Keep the existing verification deployment intact until the new test passes.

Set these public values in API and PM configuration:

```dotenv
SWITCHCREW_PM_AGENT_ID=PM_REGISTRATION_ID
SWITCHCREW_ENGINEERING_AGENT_ID=ENGINEERING_REGISTRATION_ID
SWITCHCREW_ENGINEERING_NAME=engineering-agent
SWITCHCREW_QA_AGENT_ID=QA_REGISTRATION_ID
SWITCHCREW_RELEASE_AGENT_ID=RELEASE_REGISTRATION_ID
SWITCHCREW_RELEASE_NAME=release-agent
SWITCHCREW_RELEASE_REPOSITORY=OWNER/REPOSITORY
SWITCHCREW_RELEASE_BASE=master
```

All four identities must differ. Names are exact Switch machine names; IDs are
registration IDs, distinct from transport participant IDs. Register Engineering,
QA, Release and a dedicated Console Codex agent as described in
[Switch runtimes](AGENT_SWITCH.md) and [Engineering/QA](ENGINEERING_QA.md).

For Discord approval, configure `SWITCHCREW_APPROVAL_USER_IDS` on PM with the
Discord user IDs allowed to approve either stage. These users also need verified
`DISCORD_BINDINGS` and the ordinary guild/user ingress allowlists. Identity comes
from the authenticated Switch/Discord binding, never an `actor` field in JSON.
The decision must be in the original task thread (or original message's thread).
Revocation is operator configuration; live Discord role lookup is not implemented.

For API approval, configure **only the API process** with
`SWITCHCREW_APPROVAL_CREDENTIALS`, a JSON map of human identity to an independent
random token of at least 32 characters. The normal service API token cannot
approve. Do not share human credentials with agent workers. Compose passes these
credentials only to API; the GitHub credential belongs only to Release's private
config. Generate credentials locally and keep `.env`/config files private.

The durable request binds the job key, specialist identities and destination
repository/base. Release refuses a destination changed after the request.
`job_key` selects an operator-owned template; request JSON cannot supply shell
commands, credentials, source paths or publication destinations.

## Independent worker configuration

Each process uses a chmod-600 config and its own chmod-600 `AgentConnection`
registry. Use the same `test_room_id` as PM's configured room for this workflow.
Engineering allows PM and QA transport participant IDs; QA allows Engineering;
Release allows PM. Map each to its actual registration ID in `allowed_senders`.
Configure `target_names`: Engineering → PM and QA, QA → Engineering, Release → PM.
PM's target names for Engineering and Release come from the settings above.
The managed Codex machine must also be reachable from Engineering in this room.

Engineering config:

```json
{
  "role": "engineering",
  "registry": "/private/crew/engineering-registry.json",
  "test_room_id": "WORKFLOW_ROOM",
  "switch_url": "http://127.0.0.1:8080",
  "database": "/private/crew/application.sqlite3",
  "ledger": "/private/crew/executions.sqlite3",
  "checkout_root": "/private/crew/engineering",
  "control_file": "/path/to/Console/control-api.json",
  "console_database": "/path/to/Console/switchdash.db",
  "job_templates": {
    "fix-answer": {
      "source": "/absolute/target-repository",
      "base_revision": "FULL_PINNED_BASE_SHA",
      "console_agent_id": "DEDICATED_CONSOLE_CODEX_AGENT_ID",
      "codex_switch_agent_id": "CODEX_SWITCH_REGISTRATION_ID",
      "codex_name": "engineering-codex-test"
    }
  }
}
```

QA uses role `qa`, its own registry and a different checkout root, the same
application DB/ledger, and an independently owned template:

```json
{
  "job_templates": {
    "fix-answer": {
      "commands": [["/absolute/python", "-m", "pytest", "-q"]],
      "timeout": 120
    }
  }
}
```

Use the same job key on both workers. QA derives the implementation assignment
and exact SHA from the persisted workflow. Commands come only from QA's config.
Missing templates/commands block execution. The old standalone `jobs`/`reviews`
keys and a manually seeded handoff do not bypass human approval.

Release config needs its registry, role `release`, room, Switch URL, shared DB
and ledger, plus this process-private section; it needs no `job_templates`,
Console paths or checkout root:

```json
{
  "release": {
    "repository": "OWNER/REPOSITORY",
    "base": "master",
    "token": "CONFIGURE_LOCALLY"
  }
}
```

Use a repository-scoped GitHub credential with Contents read/write and Pull
requests read/write. GitHub must support draft PRs for that repository/account.
The adapter uses the official [pull request REST API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)
with `draft: true`, and Git's HTTPS transport for the exact approved SHA.
The source repository's hooks, remotes and credentials are not used for the push:
Release fetches the commit into a fresh bare repository with a minimal environment
and applies the token only to the HTTPS push process. No force-push is used.

Prepare the assignment clone and configure Console's dedicated local location at
`<checkout_root>/<assignment_id>` before delivering work to Engineering. Obtain
`engineering_handoff` from the approved workflow and derive `assignment_id` using
`switchcrew.engineering.assignment_id`. The read-only preparation call with
`session=None` described in ENGINEERING_QA.md remains available to a trusted
operator; it creates no managed session. To allow setup time, approve while the
Engineering runtime is stopped, prepare/onboard Console, then start it. Do not
start delivery with incomplete configuration: a blocked handoff requires a new
complete task, rather than mutating immutable assignment inputs.

Start each independent worker:

```sh
uv run python -m switchcrew.engineering_qa --config /private/crew/engineering-config.json
uv run python -m switchcrew.engineering_qa --config /private/crew/qa-config.json
uv run python -m switchcrew.engineering_qa --config /private/crew/release-config.json
```

PM polls durable state while the stream is quiet, posts QA/release-review notices
and relays the final receipt to the original thread. Specialist execution and
outbox delivery wait for room readiness and pause on disconnect. Use an
`on-failure` supervisor; lost ownership exits zero to stand down.

## Requests and decisions

Send the following JSON through the verified Discord binding to PM:

```json
{
  "kind": "engineering",
  "job_key": "fix-answer",
  "question": "Correct answer.txt",
  "acceptance_criteria": ["Answer equals correct and the tests pass"]
}
```

The PM replies with task ID and version. An allowlisted approver sends a separate
message in the same thread:

```json
{
  "type": "approval",
  "task_id": "TASK_ID",
  "stage": "engineering",
  "approved": true,
  "expected_version": 1
}
```

For release use `stage: "release"` and the current `HUMAN_REVIEW` version after
reviewing the tested SHA, QA commands/results, repository and base. Use
`approved: false` to reject either pending stage. An `approved` field on the
original engineering request provides no authorization.

API equivalents:

- `POST /tasks` with service bearer token: `kind: "engineering"`, `job_key`,
  `objective`, `acceptance_criteria`, and optional existing `source` fields.
- `GET /tasks/{id}` and `GET /tasks/{id}/workflow` with service bearer token to
  inspect the current version, durable approvals, revision, QA result and receipt.
- `POST /tasks/{id}/approvals` with the **human** bearer token and
  `{ "stage": "engineering", "approved": true, "expected_version": 1 }`.
  Release/rejection use the same endpoint. The API derives the actor from the
  credential; an actor field is refused. Stale/replayed decisions return 409;
  missing/service credentials return 403.
- `GET /tasks/{id}/events` provides the timestamped decision/QA/release audit trail.

Task state sequence:

```text
AWAITING_APPROVAL → IN_PROGRESS → QA_REVIEW → HUMAN_REVIEW
→ PUBLISH_APPROVED → PUBLISHING → PR_OPEN
```

Intermediate `PLANNED` and `PASSED` transitions are also audited. Failed QA moves
to `FAILED`; rejection moves to `CANCELLED`. A failed/rejected assignment requires
a new task for a corrected request. The final receipt includes the full commit
SHA, actual command evidence, Engineering approval, release approval/timestamp
and PR URL. Its concise Discord form is split into bounded thread messages;
full evidence remains available through the authenticated API/events.

## Recovery and live acceptance

Release uses a deterministic branch and durable execution claims. An uncertain
push reconciles the remote branch, refuses a different SHA, and can retry the
same exact-SHA push. PR creation is claimed before POST. A lost response triggers
read-only PR lookup by branch/base, including closed PRs. Only one open draft
with the approved SHA/destination can become a receipt. An absent/ambiguous,
closed, converted or changed PR requires operator reconciliation; no second
creation POST is sent blindly. Do not erase the ledger to retry uncertainty.

Cancellation is allowed until Release transactionally claims `PUBLISHING`.
After that point the API refuses cancellation, because a publication request
may already be in flight. Finish/reconcile it and review the resulting draft.
Engineering cancellation checks cannot recall a Console request already in
flight. Checkouts and QA subprocesses are not an OS security sandbox; use a
dedicated account/container/VM for untrusted code, as described in ENGINEERING_QA.md.

Controlled tests use real Git commits, fresh QA clones and subprocess assertions,
and controlled Console/GitHub adapters. They cover rejected/forged/stale approvals,
failed QA, changed revisions/destinations, the configured three-worker runtime,
receipt persistence, lost PR responses and an actual local Git push through the
fresh-bare staging path. They do not prove live Console or GitHub acceptance.

On the verification host, record a real PM → approved Engineering → QA → release
approval → GitHub draft PR, with task/handoff/session IDs, final/tested SHA,
commands, both approvers and actual PR URL. Separately demonstrate no session
before approval, rejected Engineering/release requests, failed QA preventing
publication, and restart reconciliation. This checkout has not performed those
live actions or created a real PR.
