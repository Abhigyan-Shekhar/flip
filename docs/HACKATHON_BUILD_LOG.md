# SwitchCrew build log

## November 3, 2026 IST — implementation before the event

The user explicitly instructed work to start immediately after the earlier time
gate stopped implementation. The November 4, 2026 00:01 IST event boundary had
not started. These changes must not be represented as event-built. No dates were
backdated, and no commit or push was made by this work.

The working checkout had changed since the earlier audit: implementation began
from a clean master at `364d2daf90b7382eee8a1438c924ead9fae55c82`, incorporating
the earlier modified/untracked files. Existing `.env`, `.switch/`, databases,
credentials, commits and deployments were preserved.

Six Luna workers contributed in two waves of three, with parent integration and
review. Phase 1 established the separate Reviewer capability and persisted
assignments, mandatory exact-revision review before release, bounded correction
attempts, current-commit QA, cumulative original-base diffs and expanded receipts.
The first combined Phase 1 checkpoint passed 278 tests, with 3 live tests skipped.
Ruff lint/format and diff checks passed. Further parent regression coverage added
an actual SIGKILL at the correction transaction boundary and recovery to one
logical assignment; controlled Console/Switch/GitHub fixtures remain distinct
from real service evidence.

Phase 2 added opt-in incident intake, preliminary classification/severity,
source and thread correlation, rate limits, normalized report deduplication,
capability selection, bounded team amendments, and original-thread crew text
commands. Incident child Engineering tasks use the Phase 1 approvals and checks.
Parent review fixed replay recovery, false task creation after rejected intake,
duplicate specialist identities, and redaction of hashed handoff identifiers.

Read-only readiness probes on this host could not connect to Switch at
127.0.0.1:8080 or application health at 127.0.0.1:8000. Docker was unavailable on
PATH. No managed Codex session, real Switch incident exchange, real QA/reviewer
result, release approval, branch push or PR creation was attempted or claimed.
The earlier verification Mac's records remain historical evidence.

Final local verification is recorded in IMPLEMENTATION_STATUS.md. A future
live run must capture its actual timestamps, revisions, independent identities,
message/handoff/session IDs, commands, approvals, and fetched draft PR head.

Final local checkpoint: 306 passed, 3 live tests skipped in 28.47 seconds;
Ruff lint, formatting (57 Python files), and git diff --check passed. Full file
inventory and remaining scope are in IMPLEMENTATION_STATUS.md. No commit was
made; this evidence refers to the uncommitted working tree.

## November 3, 2026 IST — Company Brain follow-up

The user authorized committing/pushing existing work and starting Company Brain
integration with Luna agents. Prior work was pushed as `82e3a99`. Three Luna
agents handled the adapter, runtime integration and independent review, with
parent integration. This remains pre-event work. No registry refresh, deployment
or live Company Brain/Switch acceptance is claimed. See COMPANY_BRAIN.md.

Company Brain local validation: 326 passed, 3 live tests skipped; Ruff check,
format check (61 Python files), and diff check passed. Controlled MCP stdio
transport coverage passed. No live graph acceptance or external runtime changes.
