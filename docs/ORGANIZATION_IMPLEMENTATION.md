# Organization upgrade — implementation and verification

**Historical implementation snapshot.** This matrix separates code coverage from
acceptance at the time it was written. Later Mac setup, merged PRs #8–#10, real
Discord PM→Research and temporary slash verification, and isolated QA/Reviewer
component results are recorded in [the November 4 report](VERIFICATION_2026-11-04.md).
Earlier absent-Docker/uncommitted statements below are historical. Full managed
coding/release, audio/voice and graph/learning acceptance remain pending.

Work extends the existing `Abhigyan-Shekhar/flip` checkout at `d53c4bc`. The checkout
was clean before this task. Three Luna agents implemented separate areas; the
parent integrated the existing API, PM, specialist and Engineering paths and
reviewed their changes. No new backend or hosted infrastructure was introduced.
This working tree has not been committed, pushed, deployed or externally published.

## Audit and implementation checklist

The existing code already had independent Switch/LangGraph workers, SQLite
inbox/outbox/checkpoints, Console-managed Engineering, independent QA/Reviewer,
revision-bound approval/release gates, bounded corrections, incident teams,
Company Brain adapters, scoped Waggle recall and consented short-file voice I/O.
This task adds organization workflows to those mechanisms rather than replacing
them. Existing live records belong to their recorded host/revision and do not
establish current-host acceptance.

- Extend recorded audio into independent Voice → Notes → confirmed PM tasks.
- Expand channel-aware Discord task views and filtered durable execution traces.
- Generate real Git change/attribution drafts and Company Brain impact context.
- Add signed GitHub test triggers, configured scanners and an optional mandatory gate.
- Add approved document ingestion, actual scope boundaries and verified lessons.
- Demonstrate two-task behavioral lesson reuse with explicit fixture disclosures.
- Bind employee runtimes and add OSS intake to the same approval core.
- Add an independent watchdog and truthful setup/acceptance documentation.

## Architecture and feature matrix

| Capability | Local implementation | Remaining boundary |
| --- | --- | --- |
| Recorded meetings | Bounded WAV recording jobs, revocable participant consent, independent Voice Switch handoff, timestamped anonymous-speaker STT and provenance | Live provider and consented real audio acceptance need operator credentials |
| Notes → PM | Independent Notes runtime, professional minutes, speaker-wise summaries, transcript evidence refs, edited human confirmation and durable downstream handoffs | Summaries are extractive drafts; ambiguous identities and commitments need human resolution |
| Meeting fact checking | Conservative exact comparisons against scoped durable facts; graph/lesson evidence retains trust labels | Broad semantic fact checking is partial; source coverage can be missing and model inference is never called verified |
| Presence and clarifications | Persisted authenticated event service, join/leave/reconnect/absence calculations; post-meeting clarification output | Actual Discord voice-state adapter and live interruption policy are not deployed; no claim about why someone left or what they heard |
| Discord workspace | Create/list/mine/completed/status/dependencies/evidence/trace commands, channel/thread checks and cancellation | Text commands; registered application commands and automatic channel provisioning are not implemented |
| Change intelligence | Exact before/after Git diff, digest, files, Python AST symbol changes, author, workflow receipts and Markdown drafts | Static Python symbol coverage; downstream graph coverage is unknown when Company Brain cannot verify it; no auto publication |
| Triggered verification | Signed repository-scoped push/PR intake, semantic dedupe, independent QA/Security handoffs and exact-SHA isolated checks | Exact commits/scanner binaries must be available locally; untrusted code still requires host/OS isolation for production |
| Security gate | Persistent optional project policy, current-revision independent Security assignment, missing-scan failure, severity threshold and mandatory release evidence | No scanner is installed or certified by default; unavailable scanners block; policies require operator configuration |
| Knowledge ingestion | Approved md/txt/json snapshots with owner/source/timestamp/digest/project/scope/provenance, default-deny actor checks | Human approval is a review state, not automatic truth verification; external AI account scraping is not performed |
| Memory scopes | Personal/team/org SQLite ACLs and project-only approved lesson projection to Waggle | Current Waggle API lacks these finer ACLs, so private/team/org records remain local; no graph edges are fabricated |
| Verified lessons | Actual failed/corrected revision evidence, final QA/Reviewer regression, independent human approval, local contradiction/supersession, retrieval and use receipts | Live Waggle persistence/reuse remains unverified here; agent use claims alone are not proof of causal behavior |
| Employee Codex | Explicit employee assignment, distinct worker/provider identities, workspace confinement, private Console profiles and actual session tracking | Separate OS accounts/filesystem restrictions and real employee Console providers are still required; multi-host connections and autonomous peer-to-peer employee delegation are partial, with privileged work routed through PM approvals |
| OSS maintainer | Signed authorized issue intake, uncertain duplicate groups, immutable issue snapshots, Research→approval-gated Engineering using the same core | No external issue comments/closing/merging/deployment; live issue-to-approved-draft-PR acceptance is pending |
| Watchdog/recovery | Independent CLI, runtime heartbeats, bounded durable findings and deduplicated alerts for stalls/uncertain effects | It observes and reports; it never blindly retries external actions or repairs arbitrary production failures |
| Execution trace | Source references, task lineage, plans, assignments, revisions, outcomes, approvals and sidecar references via Discord/API | Raw memory/transcripts/command output/diffs are omitted; the trace cannot invent missing evidence |
| Live voice | Explicit blocked result and current DAVE research | Verified live receive, multi-speaker consent, membership transitions, reconnect/stop, and actual voice transport remain blocked |

## Acceptance scenarios

| Scenario | Evidence |
| --- | --- |
| A: recorded meeting → notes → confirmed task → Switch | `test_notes_switch_pipeline.py` exercises actual AgentRuntime graphs and controlled target delivery for Voice, Notes and the confirmed Research handoff. STT is a fixture; no live Discord/provider claim. |
| B: QA failure → correction → fresh QA/Reviewer → approved draft PR | Existing correction/release tests exercise real Git and subprocesses with controlled Console/Switch/GitHub transports. Independent Security gate tests cover the additional configured release boundary. Real Console and GitHub acceptance remains blocked. |
| C: code change → impact → documentation | Real Git diff and AST tests plus adapter revision/scope tests; missing Company Brain evidence remains unknown. No live graph response for this repository is claimed. |
| D: failure → memory → later reuse | `learning_acceptance` creates real temporary Git repositories, failing/passing subprocess regressions, approved lesson receipts and observed behavioral change through controlled memory. It reports `live_waggle_called=false`. |
| E: issue → investigation → approved fix workflow | Signed issue tests exercise scoped intake, duplicate uncertainty, replay, Research dependencies and Engineering approval creation. The later core correction/release stages have their own controlled tests; a complete live issue-to-PR run remains pending. |
| F: disconnect/restart | Existing Switch/checkpoint crash tests plus meeting, employee, trigger, lesson, policy and watchdog SQLite replay tests. No new live deployment recovery run occurred. |
| G: unauthorized privileges/private knowledge | API role separation, cross-personal knowledge denial, forged identity rejection, proposal confirmation, signature/repo scope checks, employee confinement/revocation and release-gate tests. |

## Reproducible local checks

```sh
.venv/bin/python -m pytest -q --tb=short
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
git diff --check
uv build --out-dir /tmp/switchcrew-organization-build
PYTHONPATH=src .venv/bin/python -m switchcrew.learning_acceptance
```

For the meeting vertical slice alone run
`pytest -q tests/test_notes_switch_pipeline.py tests/test_organization_api.py`.
Configure a real deployment using [Organization setup](ORGANIZATION_SETUP.md),
then record provider/session/Switch/message IDs and exact revisions for each live
scenario. Tests and local health are not substitutes for those records.

## Live prerequisites and risks

The current host preflight found no working Docker/Compose runtime and could not
reach Switch, gateway or SwitchCrew health. Required Switch room/identity/binding
and Research-source values are absent from private local configuration. Existing
verification-Mac records are historical. Company Brain does not register this
checkout in the connected registry. No external credentials were created or
changed and no provider, GitHub, Discord or production Waggle mutation was made.

Live voice must support Discord's DAVE protocol. Discord documents mandatory E2EE
support in its [voice connection documentation](https://github.com/discord/discord-api-docs/blob/main/developers/topics/voice-connections.mdx)
and [rollout announcement](https://discord.com/blog/every-voice-and-video-call-on-discord-is-now-end-to-end-encrypted).
The repository's isolated receive probes remain research evidence, not a verified
live transport. Recorded-file processing is usable independently.

Configure separate identities, room addressing, Notes/Voice runtimes, provider
consent, exact checkouts, meaningful test/review/scanner commands, scoped Waggle,
Company Brain registry/index and authorized GitHub webhook/release credentials.
Keep application data/configuration private and define retention for transcripts,
imports and artifacts. A working tree/clone is not an OS or network sandbox.
The recommended next priority is a real recorded-meeting deployment run, followed
by a current exact-SHA Console→QA→Reviewer→Security→human-approved draft PR run.

## Timing integrity

Implementation began on November 3, 2026 IST before the event boundary. Work
continued on November 4 after the user resumed it; the first resumed clock check
was November 4 01:24:21 UTC (06:54:21 IST). This uncommitted tree contains work from
both intervals and must not be described as wholly event-built. No history or
timestamps were rewritten, and no event eligibility claim is made.

## Final local verification

Recorded 2026-11-04T01:58:07.572313+00:00. Final full suite: **480 passed, 3 live tests skipped**
in 42.56 seconds. Ruff lint, formatting (104 Python files), `git diff --check`,
and `uv build` passed. Both wheel and source archive were built under
`/tmp/switchcrew-organization-build`. The standalone controlled two-task learning
CLI returned **PASS**, showing a failing baseline and passing post-retrieval
regression with an `applied_and_verified` receipt; `live_waggle_called` was false.

Current-host preflight remains **BLOCKED**: Docker/Compose is unavailable, Switch/
gateway/application endpoints are unreachable, and required private Switch/Discord
identity/binding/source configuration is missing. The Company Brain registry still
contains other repositories and does not register this checkout. No new live
provider, Console, Discord, Company Brain, Waggle or GitHub acceptance is claimed.

The full autonomous product is not declared complete: live voice, registered
application commands, broad semantic meeting interpretation, multi-host/private
employee deployment, autonomous employee peer delegation and complete real
external acceptance remain partial or blocked as detailed above.

## Changed files

- `.env.example`
- `README.md`
- `compose.yaml`
- `docs/AGENT_WORKFLOWS.md`
- `docs/CHANGE_INTELLIGENCE.md`
- `docs/EMPLOYEE_AGENTS.md`
- `docs/HACKATHON_BUILD_LOG.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/KNOWLEDGE_LEARNING.md`
- `docs/MEETINGS.md`
- `docs/ORGANIZATION_IMPLEMENTATION.md`
- `docs/ORGANIZATION_SETUP.md`
- `docs/OSS_MAINTAINER.md`
- `docs/TRIGGERED_CHECKS.md`
- `src/switchcrew/agents.py`
- `src/switchcrew/api.py`
- `src/switchcrew/approval.py`
- `src/switchcrew/change_intelligence.py`
- `src/switchcrew/company_brain.py`
- `src/switchcrew/crew.py`
- `src/switchcrew/employees.py`
- `src/switchcrew/engineering.py`
- `src/switchcrew/engineering_qa.py`
- `src/switchcrew/graphs.py`
- `src/switchcrew/incidents.py`
- `src/switchcrew/knowledge.py`
- `src/switchcrew/learning_acceptance.py`
- `src/switchcrew/learning_runtime.py`
- `src/switchcrew/lessons.py`
- `src/switchcrew/limits.py`
- `src/switchcrew/meeting_context.py`
- `src/switchcrew/meetings.py`
- `src/switchcrew/notes.py`
- `src/switchcrew/orchestration.py`
- `src/switchcrew/organization.py`
- `src/switchcrew/organization_api.py`
- `src/switchcrew/organization_runtime.py`
- `src/switchcrew/oss.py`
- `src/switchcrew/runtime.py`
- `src/switchcrew/security.py`
- `src/switchcrew/security_gate.py`
- `src/switchcrew/settings.py`
- `src/switchcrew/trace.py`
- `src/switchcrew/triggers.py`
- `src/switchcrew/voice.py`
- `src/switchcrew/watchdog.py`
- `src/switchcrew/wire.py`
- `src/switchcrew/workers.py`
- `tests/test_change_intelligence.py`
- `tests/test_company_brain.py`
- `tests/test_employee_api.py`
- `tests/test_employee_runtime.py`
- `tests/test_employees.py`
- `tests/test_knowledge.py`
- `tests/test_learning_acceptance.py`
- `tests/test_learning_runtime.py`
- `tests/test_lessons.py`
- `tests/test_meeting_context.py`
- `tests/test_meetings.py`
- `tests/test_notes.py`
- `tests/test_notes_switch_pipeline.py`
- `tests/test_organization_api.py`
- `tests/test_organization_security_flow.py`
- `tests/test_oss.py`
- `tests/test_security.py`
- `tests/test_security_gate.py`
- `tests/test_task_workspace.py`
- `tests/test_triggers.py`
- `tests/test_voice.py`
- `tests/test_watchdog.py`
