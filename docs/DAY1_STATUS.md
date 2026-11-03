# Day 1 implementation and review — October 29, 2026

## Integration update — November 3, 2026

Merged the checkout/onboarding branch through `6b93d2b` into local `master` and
retained the separately recorded recovery evidence through merge `ba35bff`.
Three requested subagents implemented/reviewed recovery, Voice, and specialist
executors; the coordinating agent integrated runtime/PM behavior and reviewed
cross-agent authorization and cancellation.

| Workstream | Local result | Live acceptance boundary |
| --- | --- | --- |
| Checkout/onboarding | Merged private `.switch/config/` exclusion and tracked-credential rejection; regression tests pass. | Retry a new genuinely human-approved managed assignment on the verification Mac; the failed prior task remains evidence. |
| Recovery | Fixed clean-EOF/heartbeat takeover race without weakening the one-open assertion. Added real SIGKILL transaction/outbox recovery checks. | Earlier live record had 2 heartbeat probes pass and strict takeover fail. Candidate fix needs the same live probe rerun; no Switch services are reachable here. |
| Voice | Real ElevenLabs file STT/TTS adapters, explicit revocable per-subject consent, bounded inputs/outputs, private artifacts and durable billed-call claims. | No paid provider request or Discord audio capture was performed. Live DAVE receive remains unverified. |
| Specialist executors | Nine roles now compute cited documentation/support/knowledge/onboarding drafts, moderation/security reviews, scheduling conflicts, analytics and incident timelines. Private-config CLI wiring is tested. | These are read-only local snapshot analyses/drafts, not external mutations or proof of deployment in Discord. |
| Waggle | Real bundled MCP server stored an Engineering fixture and a fresh Documentation client recalled it across sessions on November 2. | Isolated synthetic acceptance scope; this does not enable memory on the other Mac's Discord workers. |
| PM orchestration | Bounded criterion-based decomposition, stable dependent children, immutable routes, durable dispatch/results and child Engineering approval prompts. | Deterministic rules rather than an LLM planner. Live multi-specialist Discord acceptance still needs provisioning. |
| Research/documentation | Criteria-aware whole-token ranking, deduplication, bounded Markdown excerpts, criterion evidence links and cited document artifacts. | Evidence and completed drafts still require human criterion assessment. |

Integrated verification: **258 tests passed; 3 live tests skipped**. Ruff lint,
format, lockfile and diff checks passed; source/wheel builds and installed CLI
smoke checks passed. No private acceptance database or provider configuration is
included. On this host, refreshing an existing noneditable installation required
`uv sync --frozen --no-editable --reinstall-package switchcrew`.

Integration review fixed Research-completion notification ordering, missing
Engineering-child approval prompts, inactive-parent delivery/execution, and QA
revocation between commands. Parent cancellation is transactionally refused
while a child publication is in flight. Existing external work already in flight
cannot be recalled by a database update.

Runbooks: [orchestration](ORCHESTRATION.md), [specialists](SPECIALISTS.md),
[Voice](VOICE.md), [memory evidence](MEMORY.md#live-shared-memory-acceptance),
[recovery](../deploy/switch/RECOVERY_ACCEPTANCE.md), and
[approval/release](APPROVAL_RELEASE.md). Earlier reports below retain their
original dates, test counts and live scope.

## Approved Engineering and Release — November 1, 2026

PM now accepts explicit engineering requests, waits for a separately authorized
human decision, and creates the Engineering handoff only on approval. Durable
workflow records drive independent revision-bound QA, a second human decision,
and the Release worker's exact-SHA branch publication and GitHub draft PR.
Receipts include both approval identities, the tested SHA, command evidence and
PR URL. Service credentials cannot approve, rejection cancels, failed/stale QA
blocks release, and uncertain PR creation is reconciled without a duplicate POST.

The [current setup and acceptance runbook](APPROVAL_RELEASE.md) supersedes the
standalone/no-routing statements in the historical integration report below.
Local tests use actual Git commits and QA subprocesses with controlled Console
and GitHub adapters. Real Console/GitHub/Discord acceptance remains unverified
on this host. The user's later pasted Discord transcript supports the existing
PM/Research baseline and one visible forced-gap recovery sequence on reported
`50e24b2`; see the [operator evidence](../deploy/switch/VERIFICATION.md).
That transcript does not demonstrate this new approval/release implementation.
No live PR was created and no running deployment was reconfigured.
Validation: **189 tests passed; 3 live tests skipped**. Ruff lint/format, lockfile
and diff checks passed; wheel/source builds and both worker CLI smoke checks
passed. The CLI checks used the documented noneditable-install workaround.

## Combined recovery and Engineering/QA integration — November 1, 2026

Integrated Person 1's `8506a38` and Person 2's `0b1dce7`, retaining their shared
live-verification base `2441365`. Two explicitly requested GPT-6.1 Terra
subagents independently reviewed Engineering/QA integrity and runtime/state
recovery; the coordinating agent integrated their fixes and reviewed the whole.

Review fixes include fresh revision-pinned QA clones per command and rejection
of generated inputs, transactional terminal-task checks before creating QA work
or committing late executor results, continued reconciliation after temporary
Console outages, and malformed receipt handling. Integration also pauses work
and queued delivery when transport liveness is lost, waits for room readiness,
validates Switch origins and uses sanitized takeover-aware CLI exit policies.
The dedicated Research worker uses the same terminal-aware result commit.

Combined local validation: **171 tests passed; 3 opt-in live tests skipped**.
Ruff lint/format, lockfile verification, shell syntax and diff checks passed;
source distribution and wheel built, and standalone Engineering/QA and generic
runtime help entry points were exercised. No credentials or private state were
included. QA commands are trusted operator configuration, not an OS sandbox;
see the per-command isolation policy in [ENGINEERING_QA.md](ENGINEERING_QA.md).

Real Console Codex → independent QA acceptance and the remaining Discord
recovery gates still need the verification Mac and its private configuration.
The default Discord PM worker still delegates only to Research; Engineering/QA
is a separately configured entry point, not automatic approved PM routing.
Running external work and network requests already in flight cannot be recalled
by the SQLite cancellation checks. Earlier counts and reports below retain
their original scope and dates.

## Current follow-up — November 1, 2026

The live-verification branch (`2441365`) recorded a real Discord → PM → Research
→ original-thread exchange, worker/API restart persistence, PM/Research disconnect
recovery and a real server gap caused by a missing replay ticket. Read the
[verification record](../deploy/switch/VERIFICATION.md) for exact evidence and
limits. Infrastructure and credentials remain on the verification Mac.

The `codex/recovery-acceptance` follow-up adds fenced reattachment, recoverable
heartbeat-lapse handling, takeover-aware worker exit status and an opt-in
`on-failure` Compose override. It includes role reacquisition, missed-request
replay/deduplication, interrupted replay persistence and safe diagnostic tests.
Opt-in real-server heartbeat tests and a reversible cursor injection procedure
are in the [acceptance runbook](../deploy/switch/RECOVERY_ACCEPTANCE.md).

The current development host lacks Docker, worker credentials and reachable
Switch services; the user confirmed the recorded deployment was on a friend's
Mac. No new live acceptance is claimed here. Request-during-gap, explicit live
heartbeat cases, fresh deployment reproducibility and the desktop Console
connection remain open. The historical report below retains its original counts
and pending rows rather than presenting them as current status.

## Historical implementation report

This is the historical Day 1 implementation report. The user subsequently
requested [LangGraph scaffolding, product Waggle memory and all-role Switch
runtimes](AGENT_WORKFLOWS.md); those additions supersede the deferred-scaffolding
notes below. The current test suite and follow-up evidence are recorded there.

Three GPT-6.1 Sol agents implemented persistence, Switch/Discord transport, and
PM/Research workers in parallel. The coordinating agent implemented the API,
configuration, logging, deployment assets and documentation, integrated their
work, reviewed all nine workstreams and fixed review findings. The user confirmed
that no Switch server or Discord credentials are configured and requested local
completion plus live setup documentation.

## Nine-workstream status

| # | Workstream | Local result | External acceptance still pending |
| --- | --- | --- | --- |
| 1 | FastAPI backend | Python 3.12/uv lockfile, authenticated task API, health, Pydantic request/response schemas, settings, sanitized JSON logs/errors, pytest/Ruff, env example, Docker/Compose | Container build/start on a Docker host |
| 2 | Persistence | SQLite v1 migration, tasks/events/assignments/handoffs, inbox/outbox, optimistic versions, atomic updates/events/outbox, stable IDs, restart and concurrency checks | Shared-volume deployment validation |
| 3 | Switch | Pinned-source HTTP operations and registration, SSE parser, stable connection IDs, generation heartbeat, targeted names, trusted sender checks, cursor/history replay and retry-safe durable work | Real deployment, registration, room/role/address policy and live connectivity |
| 4 | Discord | Built-in bridge setup guide, verified opaque identity bindings, guild/user allowlists, original-thread routing and distinct specialist identities | Actual test guild/bot/intents/permissions, owner link, bridge room and webhook appearance |
| 5 | PM | Independent process, persistent request, requirement checks, clarification, addressed handoff, acknowledgment/result tracking and thread replies | Real Discord ingress and delivery |
| 6 | Research | Independent process, trusted addressed request, explicit acknowledgment before retrieval, bounded configured HTTPS evidence workflow, structured results and replay deduplication | Real Switch identity/connectivity and source retrieval through that workflow |
| 7 | Flagship workflow | Deterministic PM → Research → PM exercise with acknowledgment, source excerpts, thread routing and restart | Real Discord → Switch → both workers → original Discord thread evidence |
| 8 | Reliability | 47 deterministic tests, Ruff lint/format checks, clean install/import check and distribution build | Live Discord test, real agent lease/disconnect/gap behavior and container smoke checks |
| 9 | Deployment/docs | Local commands, configuration, API contracts, worker setup, upstream Switch/Console/Discord provisioning, Docker assets, CI and known limits | Docker startup and real-service acceptance recorded by operator |

Implementation exists across all nine workstreams. External acceptance rows are
not complete and must not be represented as passing because local mocks pass.

## Validation performed

- `uv run --no-sync pytest -q`: **47 passed**.
- `uv run --no-sync ruff check .`: passed.
- `uv run --no-sync ruff format --check src tests scripts`: passed.
- `uv lock --check`: passed.
- `uv build`: source distribution and wheel built successfully.
- Fresh temporary environment: frozen noneditable installation and package
  imports passed; full suite passed against the copied current sources.
- Installed distribution smoke check: import modules from site-packages and
  exercise authenticated task creation with real SQLite.

The suite covers API health/auth/validation/redaction and deferred-state refusal;
SQLite reopen, rollback, stale versions, schema guards, concurrent writes;
forged identities, malformed messages, different ID/name forms, explicit ack
ordering, disconnected target persistence, accepted-work recovery, replay
idempotency, thread roots/replies, SSE parsing/sequence/gap/history handling,
heartbeat retry/reconnect and connection takeover refusal. TestClient emits a
third-party deprecation warning about its httpx transport; tests pass and the
runtime HTTP client remains httpx. No live integration test was executed.

## Review findings resolved

1. SQLite shared-connection transactions could overlap in FastAPI threads:
   serialized operations and tested parallel writes and cross-connection versions.
2. Switch registration IDs, opaque participant IDs and routing names were
   conflated: configured and checked them independently.
3. Operations could run before SSE registration: moved room setup and pending
   work after `connection_state`, and made connection IDs stable across restarts.
4. Mention-prefixed targeted messages broke JSON parsing: used a fenced typed
   message envelope and canonical persisted handoff/task identities.
5. Documentation heartbeat fields differed from pinned source: send server
   generation, retry expired streams and stop after incarnation takeover.
6. Missing/blank requests and malformed payloads could poison the inbox:
   reject malformed envelopes and persist clarification requests safely.
7. Replayed stream sequence changes could conflict with inbox deduplication:
   canonicalize trusted content and use stable delivery IDs for agent messages.
8. History replay needed trusted human identity reconstruction: require a unique
   configured room/participant binding and the normal allowlists before replay.
9. API responses/validation locations and Uvicorn traceback handlers could expose
   credentials: sanitize configured secrets, omit validation inputs, and route
   server logs through the allowlisted JSON formatter.
10. Shared Compose environment leaked every credential to every process: inject
    only the API token or the corresponding worker's agent key into each service.
11. Generic transitions exposed deferred approval/publication state names:
    restrict API states to this read-only research milestone and test refusal.
12. Root-channel requests could get unrelated top-level replies: use the source
    message as thread root when the original request has no thread ID.

13. History pagination returned epoch milliseconds but accepted ISO timestamps:
    convert before requesting older pages and test multipage recovery.
14. Assignment tables existed without workflow records: create assignments with
    handoffs atomically and track accepted/completed state in the same transactions.

## Known limits and next live gate

Research is extractive matching over configured public HTTPS pages. It is not
an autonomous browser/search agent, does not synthesize a model answer, and
explicitly leaves criterion assessment to a human. `COMPLETED` means this
configured read-only retrieval finished with evidence; it is not QA approval.

Clarification currently requests a new JSON request; the earlier task remains
`NEEDS_CLARIFICATION`. Workers share application SQLite for this slice. One
instance per specialist is supported. Configured operator bindings must be
revoked when human access changes; live Discord role refresh is not implemented.

Switch POSTs have no idempotency parameter. A lost response after acceptance can
produce duplicate visible Discord posts; handoff/result persistence prevents
rerunning completed research. A crash before a retrieval result is committed
can repeat that read-only fetch. State transitions create durable `events`
outbox records as a local audit feed; no separate external event consumer ships
in this milestone.

At the initial implementation checkpoint, Docker and configured Switch/Discord
services were unavailable. A subsequent October 29 local deployment passed
Docker builds, Switch readiness, gateway login and API health; see the
[deployment record](../deploy/switch/VERIFICATION.md). The Discord/worker
acceptance sequence below remains pending.
Follow [operations](OPERATIONS.md) and [upstream provisioning](../deploy/switch/README.md)
to execute the explicit live acceptance sequence and record real task, handoff,
message and thread evidence. Later-day features listed in the request remain
out of scope. Preserve October 29 provenance when describing competition work.
