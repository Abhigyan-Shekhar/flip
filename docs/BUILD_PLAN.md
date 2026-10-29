# Event build plan

Prepared October 29, 2026. This is a plan, not evidence of implementation.
All dates below are IST. Read [technical findings](PREPARATION.md) first.

## Before November 4

Finish documentation and isolated dependency/voice feasibility investigations.
Verify Docker availability and reusable provider tooling. Preserve dated
preparation history. Do not create the competition-specific Switch rooms,
specialist registrations, or assembled workflow before the event.

## Milestones and acceptance gates

| Milestone | Dependencies | Scope | Acceptance evidence |
| --- | --- | --- | --- |
| M1 — Foundation, Nov 4 | Python 3.12, uv | FastAPI app factory, settings, health, SQLite task/event persistence, migrations, pytest/Ruff, env example, Docker | Health responds; task and event survive reopen; invalid input fails; state/event update is atomic; secrets absent from responses |
| M2 — Collaboration, Nov 4 | M1, Docker/Switch, Discord guild, agent credentials | Actual text bridge and separate PM/Research workers through HTTP/SSE | Human request, addressed PM handoff, Research acknowledgment/result, and both identities visible in the original Discord thread; persist message/task/handoff IDs |
| M3 — Specialist workflows, Nov 5 | M2, configured model | Per-specialist LangGraphs, typed contracts, QA workflow, handoff inbox/outbox | Missing requirements escalate; forged sender is rejected; duplicate delivery does not rerun work; disconnected recipient is reported; restart resumes from checkpoint |
| M4 — Tested PR, Nov 5 | M3, Switch-managed Codex, isolated target repo, GitHub access | Implementation approval, Research → Codex → independent QA → publication proposal → authorized draft PR | Real diff/revision; QA failure and correction; pass at final head; unauthorized/rejected/stale approval blocked; real draft PR fetched and verified |
| M5 — Voice, Nov 6 | M2, successful DAVE spike, voice bot, STT/TTS | Consent, receive/transmit, transcription, conversation, task confirmation | Real two-speaker VC, membership transition and reconnect; stop halts capture; unconfirmed speech never creates executable work; confirmed request enters existing Switch path |
| M6 — Evidence and recovery, Nov 6 | M4, M5 | Receipts, recovery hardening, support workflow through Community Agent | Source-thread receipt links criteria, contributions, revision, tests, approver and PR; crash after external success does not create a duplicate; text support and voice share infrastructure |
| M7 — Submission, Nov 7 | M1–M6 | Reproducible setup, security checks, demo, architecture/workflow docs | Clean documented install, full suite and lint pass, actual recorded flagship and support demos, limitations disclosed |

M1 is the first small implementation slice: authenticated task persistence plus
a health endpoint, with restart and atomicity checks. M2 is the first product
proof: a human asks in Discord, PM delegates to Research through Switch, and
Research answers in the same thread. Do not add Codex, GitHub, or voice to M2.

For M2, use the backend task ID to correlate the exchange. Record real Switch
message IDs and agent identities. Require the receiving worker's acknowledgment;
successful HTTP delivery alone is insufficient. Restart a worker and verify
pending work can be recovered without repeating completed work.

## Incremental repository shape

Create only modules needed by the active slice. Start with `pyproject.toml`,
`src/switchcrew/` for API/config/store, and `tests/`. M2 adds the Switch adapter
and worker entry point. M3 adds typed contracts and specialist graphs. M4 adds
the restricted release service and QA runner. M5 adds Discord voice adapters.
Keep Switch deployment configuration separate from application configuration.
No JavaScript backend or standalone dashboard is needed.

Implement all specified task states, but only expose transitions supported by
the current slice. Add authenticated APIs as their consumers arrive: health,
roster, task creation/detail/events, approve/reject/cancel, receipt, voice
session start/stop. Never ship a privileged placeholder endpoint that reports
success without executing a checked operation.

## Tests that gate progression

- M1: persistence after database reopen, validation and transaction rollback.
- M2/M3: handoff success, missing requirements, addressing policy refusal,
  malformed and duplicate events, heartbeat expiry, SSE gap recovery, interrupted
  graph, and real Discord-to-Switch routing in a separate live suite.
- M4: QA failed/passed with actual commands and revisions; unauthorized,
  rejected, expired and stale approvals; concurrent publication requests;
  external timeout and crash reconciliation; successful PR verification.
- M5: consent lifecycle, new member, stop, disconnect, partial transcription,
  bot speech filtering, and confirmed versus unconfirmed task extraction.
- M6/M7: both end-to-end workflows, receipt evidence completeness, clean setup.

Mock third-party APIs in unit tests. Keep live integration checks explicit and
separate. A passing mock test never establishes that Switch, Discord, Codex,
GitHub, or DAVE works. Bound agent correction loops and tool execution time;
escalate unresolved failures to a human instead of looping indefinitely.

## Scope and readiness

The seven core roles remain required: Voice, Community, PM, Research,
Engineering through Codex, QA, and Release. Waggle and Company Brain integration
in the product, extended agents, Linear, and additional dashboards stay deferred.
Waggle used by the development assistant is separate from product integration.

Voice remains a required but currently unverified deliverable. If its transport
gate fails, record that blocker immediately and resolve it before claiming V1
completion. A text-only demo is useful partial progress, not fulfillment of the
full supplied specification.
