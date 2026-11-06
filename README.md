# SwitchCrew

SwitchCrew is an AI team inside Discord. A PM plans requests and delegates to
independent specialists through Switch; LangGraph runs each role's workflow,
and Python 3.12/FastAPI with SQLite preserves tasks, handoffs and approvals.
Engineering changes require independent QA/Reviewer checks and human approval
before Release can publish a draft PR.

For direct exchanges between separately configured Codex sessions, see
[Discord peer messaging](docs/DISCORD_PEERS.md).

## Phase 1 — The Green Check That Lied

The scanner false-clean defect is reproduced and locally patched. Live Discord,
independent Switch investigation, isolated reproduction, Company Brain evidence
and an approval-gated Engineering proposal are verified. The full scanner-to-merge
story remains pending live configuration and actual human approvals. See the
[implementation](docs/PHASE1_GREEN_CHECK.md), [demo runbook](docs/PHASE1_DEMO_RUNBOOK.md)
and [acceptance report](docs/PHASE1_ACCEPTANCE.md).

## Implementation checkpoint — November 5, 2026

The `ria-kayy/Discord-server` checkout now adds configured natural-language
Security scans, scoped `/flip` task controls, graph-guided Research, bounded
QA/Reviewer logs, native scanner parsing and separately human-approved guarded
merge controls. These changes require operator setup and have automated coverage;
they have not been run as a new live Discord-to-merge workflow. See the
[implementation, acceptance matrix and phone demo runbook](docs/HARNESS_COMPLETION_2026-11-05.md),
[scanner setup](docs/SECURITY_SCANNERS.md), and [guarded merge](docs/GUARDED_MERGE.md).

## Historical deployment checkpoint — November 4, 2026

- **Running and verified on the Mac:** local Switch, gateway, API/dashboard,
  PM and Research; a real Discord request produced a cited Research reply in
  its original thread, with durable state after restart.
- **Checks passed:** 556 automated tests (3 opt-in live tests skipped), plus a
  separate 3-test real Switch recovery run. Real restricted-container QA and
  Reviewer components rejected broken/stale revisions and accepted the corrected
  sample SHA. These used operator-prepared artifacts.
- **Fixed and merged:** dashboard pending-work filtering, malformed Discord
  payload handling, Research output guidance, and verified thread mapping for
  signed slash commands (PRs #8–#10). A real `/crew status` retry passed; its
  temporary HTTP deployment was removed after testing.
- **Still to verify:** Console-managed Engineering through human-approved draft
  PR, meaningful Security scans, real consented recorded audio/live voice,
  exact-revision Company Brain retrieval and second-task learning, and persistent
  custom slash hosting. Only PM and Research are deployed; 17 role implementations
  do not mean 17 live agents. The Research draft still awaits human review.

Read the [verification report and remaining gates](docs/VERIFICATION_2026-11-04.md),
[sanitized result index](docs/evidence/verification-2026-11-04.json), and
[Mac runtime runbook](docs/LOCAL_MAC_RUNTIME.md). Credentials, runtime state and
the generated Mac process helper are private local files, not supplied by Git.
Earlier [verification records](deploy/switch/VERIFICATION.md) remain dated history.

All 17 current roles, including independent Reviewer and Notes, have their own LangGraphs. PM and Research use durable
SQLite checkpoints; configured specialists now produce cited documentation,
support/knowledge/onboarding drafts, moderation/security reviews, schedule
conflicts, analytics and incident timelines. A consent-gated Voice adapter handles
uploaded WAV transcription and generated speech. Each specialist keeps its own
Switch identity. [PM orchestration](docs/ORCHESTRATION.md) decomposes explicit
criteria into bounded dependent tasks while preserving human Engineering approvals.
Optional Waggle MCP memory provides shared recall and curated outcome storage. See
[agent workflows](docs/AGENT_WORKFLOWS.md), [memory](docs/MEMORY.md), and
[Switch runtimes](docs/AGENT_SWITCH.md).

**Demo inference without separate API keys:** [Flip Harness Runtime v1](docs/HARNESS_RUNTIME.md)
adds opt-in model-driven PM planning and specialist outputs through locally authenticated
Codex and Antigravity CLIs. Switch messages, LangGraph checkpoints, SQLite task state,
and human-gated Engineering/QA/Review/Release are unchanged. Harness outputs are drafts
for review, not trusted evidence or execution authorization.

For an honest live-host handoff, run `uv run python -m switchcrew.deployment_preflight`
and follow the [verification runbook](docs/DEPLOYMENT_ACCEPTANCE.md). Offline
readiness never substitutes for the recorded Switch, Discord, Codex, QA and PR checks.
Restricted QA/review/security container execution is described in
[Engineering isolation](docs/ENGINEERING_ISOLATION.md); enable it explicitly in
private operator policy before running untrusted code on the verification host.
For Voice + Notes, use the [recorded-meeting acceptance inspector](docs/RECORDED_MEETING_ACCEPTANCE.md)
before separately attempting live Discord capture or interactive clarifications.
The PM harness also has [per-task invocation and planning budgets](docs/PLANNING_BUDGETS.md)
with an explicitly configured, role-constrained deterministic fallback.
The [combined Waggle + Company Brain probe](docs/COMBINED_MEMORY_ACCEPTANCE.md)
requires a clean SHA-pinned checkout and keeps live cross-task learning pending.
The [Flip Control Room](docs/OPERATOR_DASHBOARD.md) adds a read-only operator
dashboard and signed, scoped Discord slash-command handling to the API.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --frozen
# First setup only: preserve an existing .env and its credentials.
test -e .env || cp .env.example .env
chmod 600 .env
# Generate a token; paste it into SWITCHCREW_API_TOKEN in .env.
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
uv run uvicorn switchcrew.api:create_app --factory --host 127.0.0.1 --no-access-log
```

`GET /health` is public. Task APIs require `Authorization: Bearer <token>`.
Keep `.env`, SQLite files, and registration key files out of Git. A local macOS
runtime that marks editable `.pth` files hidden may need `uv sync --frozen
--no-editable --reinstall-package switchcrew` after source changes before running
installed commands; tests use `src` directly.

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests scripts
uv build
```

See [local setup and API documentation](docs/OPERATIONS.md),
[Switch/Discord provisioning](deploy/switch/README.md), and
[current implementation and review status](docs/IMPLEMENTATION_STATUS.md).

## Run in Docker

After configuring `.env`:

```sh
docker compose up --build -d api
docker compose -f compose.yaml -f deploy/compose.switch-local.yaml \
  -f deploy/switch/worker-recovery.yaml --profile workers up --build -d
```

The API listens on `127.0.0.1:8000`. All three application processes use the
`crew-data` named volume. Switch runs separately with its own PostgreSQL. Workers
need a reachable agent bridge URL; container `127.0.0.1` points at the container.
Use a secured external URL or attach workers to the actual Switch Docker network
with hostname `switch` and port 8000. The provisioning guide includes a local
network override and avoids host port conflicts.
For the pinned local Switch server and gateway, run `bash deploy/switch/local.sh`.
The gateway uses `http://127.0.0.1:3001`, and Switch uses port 8080. See the
[deployment guide](deploy/switch/README.md) for Console and local credentials.
The worker recovery override restarts failures but leaves an evicted worker
stopped. Start workers explicitly after restarting Docker; remove the local
network override when using a separately configured remote Switch server.

## Independent review and corrections

Approved Engineering tasks now require a separate Reviewer identity and exact-SHA
review before human Release approval. Failed checks can request bounded corrections
through Switch; every corrected commit needs fresh QA and review. See
[configuration and verification boundaries](docs/REVIEW_CORRECTIONS.md).

## Incident intake

Opt-in support-channel reports can create durable incident plans with capability-based
teams, bounded intake, original-thread subscriptions, and approval-gated Engineering.
See [incident configuration and limitations](docs/INCIDENTS.md) and
[crew text commands](docs/CREW_COMMANDS.md).

## Scope

Default Research retrieves bounded excerpts from operator-configured public HTTPS
sources. The optional CLI harness can produce model-driven cited drafts; neither
path establishes that acceptance criteria are verified. Missing criteria trigger a
clarification response; send a new complete JSON request. [Approved Engineering and Release](docs/APPROVAL_RELEASE.md) now
connect PM to the Engineering worker, independent revision-pinned QA, human
release approval/rejection, and GitHub draft PR publication with an audit receipt.
The route requires explicit identities, operator job templates and human approvers;
unconfigured requests cannot execute Engineering or publish. Controlled local
validation covers this pipeline; real Console-managed Codex and GitHub acceptance
remain pending on the configured verification host. Live Discord voice
capture/DAVE remains unverified; the implemented
[Voice workflow](docs/VOICE.md) processes consented files and generated replies.
[Specialist outputs](docs/SPECIALISTS.md) are useful analyses/drafts rather than
external account mutations. Memory requires explicit configuration; a real
[cross-role MCP acceptance run](docs/MEMORY.md#live-shared-memory-acceptance) passed.

Work began October 29, 2026 at the user's explicit request. The earlier
[preparation notes](docs/PREPARATION.md) and [build plan](docs/BUILD_PLAN.md) remain
as dated history. The competition rules reserve Switch workflow work for
November 4–7; this earlier implementation must not be represented as event-built.
The verbatim supplied [specification](docs/SPECIFICATION.md) is preserved.

Optional repository graph context: see [Company Brain setup](docs/COMPANY_BRAIN.md).


## Organization workflows

The organization upgrade adds independent recorded-meeting Voice and Notes
handoffs, human-confirmed action proposals, channel-aware task commands, Git
change reports, signed QA/Security and OSS intake, verified lessons, employee
Console bindings, and an independent watchdog. All privileged execution retains
the existing human, QA and Reviewer boundaries.

Read [configuration](docs/ORGANIZATION_SETUP.md) and the
[feature/acceptance matrix](docs/ORGANIZATION_IMPLEMENTATION.md) for implemented,
partial and live-blocked capabilities. Temporary live Discord slash acceptance
passed on November 4; persistent custom command hosting, live DAVE receive,
host-level employee isolation and complete external acceptance remain pending.
See the [current verification checkpoint](docs/VERIFICATION_2026-11-04.md).
