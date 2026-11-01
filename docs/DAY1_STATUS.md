# Day 1 implementation and review — October 29, 2026

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
