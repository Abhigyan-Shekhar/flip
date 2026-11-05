# Discord and Switch dry-run — 2026-11-05

## Scope

Continue the existing real Discord rehearsal, evaluate deployment readiness, reproduce urllib3 issue #5181 against an isolated pinned checkout, and prepare a video using the isolated `digitalsamba/claude-code-video-toolkit` checkout. No credentials or session cookies were copied. No upstream urllib3 commit or pull request was created.

## What worked

- Switch server and gateway were running; PostgreSQL and Switch were healthy. PM, Research, Engineering, QA, Reviewer, Release, and Security worker processes were present. The live Switch API refused unauthenticated OpenAPI access with HTTP 401; no bypass or credential extraction was attempted.
- Two authenticated Discord messages were captured from the existing `Flip The Switch - Testing Server` / `#flip-test` browser session. They show the earlier natural-language Research request and a follow-up request asking Research to inspect implementation boundaries and cite Flip files. Those are actual screenshots, not composed Discord screens.
- The prior live acceptance task `c3994e63-416a-5791-b57d-43ea5c02f80e` completed the human-approved Engineering → QA correction → Reviewer → human-approved Release workflow in the disposable repository. Final SHA: `128b821bd467bd4031a6c765cef3fb8b571cfc34`. QA failed the first revision and passed the corrected one. Reviewer accepted. The independent configured secret scan passed. GitHub draft PR: <https://github.com/Abhigyan-Shekhar/flip-acceptance-20261104/pull/1>.
- The urllib3 issue reproduction ran against exact upstream baseline `a37bc93b65d6bcf00d3dc43d966bab6bd130ecd8` in a read-only, no-network Docker test container with a pinned QA image. It used a real loopback HTTP server and real urllib3 `PoolManager`/`HTTPConnectionPool`, not mocked responses.
- All repository tests passed using the project virtual environment: `.venv/bin/python -m pytest -q` → **612 passed, 3 skipped**. The async-focused conversation suite passed: **17 passed**. The macOS system `python3` lacks the configured pytest async plugin; its initial direct invocation was not a valid project test run.
- `git diff --cached --check` passed.

## urllib3 #5181 baseline result

The isolated test exercised 307 and 308 redirects with `BytesIO` and temporary files at offsets 0 and 6, plus 303 and bare-pool controls. The failing cases showed that the redirect target received zero bytes after `PoolManager` lost the body position (`received_lengths` ended in zero); the bare `HTTPConnectionPool` control preserved the 12-byte remainder. Result: **8 expected reproduction failures, 2 controls passed**, process exit 1. The result JSON and stderr/stdout remain outside Git at `~/.local/share/flip-demo/2026-11-05/evidence/urllib3-baseline-prepared.*`.

This is a baseline reproduction only. The real issue still needs a natural-language Discord request, verified assignment, and a distinct human Engineering approval before any worker changes code. The current release configuration is scoped to the Flip acceptance repository; it is not an approved release target for urllib3. A separate human release decision and correct repository-scoped Release setup would be required before publication.

## What failed or remained unverified

- After the user said Discord browser access was re-enabled, the refreshed `cua.getState()` still reported “Local browser use is disabled in Work settings” and returned no browser tabs. No further Discord message could be sent or recorded. The longer rehearsal was not completed.
- PM → Research is captured; a full multi-step orchestration involving Security, Documentation, QA, Reviewer, Release and a new upstream-task Engineering change was not demonstrated in this rehearsal.
- The Discord slash-command service, dashboard with live tasks, worker disconnect/recovery demo, live audio/transcription, and a second-task Waggle lesson changing the outcome still need dedicated real runs.
- Voice/Notes lacked a consented recording and configured provider. No audio was processed.
- The private Rejection/replay probe was not used as evidence for this rehearsal because the earlier Discord replies were malformed; a rejected approval must not be described as verified from that probe.

## Code/configuration changes

- Natural-language PM requests and explicit approval phrases now route through the same authenticated event, room, sender, version, and approval checks as structured messages. Agent-to-agent Discord output uses readable prose and an opaque durable reference; the full task brief remains private to the assignment runner. The targeted suite and full suite passed.
- Added a reproducible issue #5181 loopback test and pinned isolated runner/controller image inputs on the current `codex/discord-demo-video` work branch. These are uncommitted. The live runner configuration and Console employee records are private local state; credentials remain outside Git.
- Installed the official video toolkit into the private demo directory at revision `b0f64a953b6888de4ca3a655be0f9c81cd1eab6b` after reviewing its migration targets and isolating its settings. The migration log and backups are outside Git. Two short authentic Discord capture segments exist privately; they are not a substitute for the unrecorded workflow.

## Build history

The Flip agents, Switch integration, Console integration, isolation controls, and most implementation infrastructure predate this hackathon. This log describes only hackathon-specific acceptance work and verification performed on 2026-11-05; it does not relabel the existing product build as hackathon implementation.

## Remaining release/demo blockers

1. Codex browser access must actually become available in the current tool session before more Discord interactions and screen capture can be completed.
2. An approved, task-specific Engineering request for urllib3 needs to be submitted and approved in Discord. The existing Release target must not be reused for upstream urllib3 publication.
3. Complete QA, independent review, and human Release approval for any proposed code change.
4. Provide a real participant-consented audio file and configured speech provider for Voice/Notes, if those are to be shown.
5. Capture, narrate, render and visually review the complete and short videos from actual footage. No final MP4 is claimed by this report.
