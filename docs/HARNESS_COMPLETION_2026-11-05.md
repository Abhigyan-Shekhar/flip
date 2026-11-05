# Discord-native harness implementation checkpoint — November 5, 2026

This change is confined to `ria-kayy/Discord-server`, on
`codex/security-to-merge`, starting at `010fd3d`. The supplied mission named a
different repository; the user's explicit checkout restriction takes precedence.
Historical Flip acceptance reports describe earlier runs and do not verify this
change. This checkpoint is an implementation increment, not a claim that all
thirteen mission phases have passed live acceptance.

## Implemented changes

- Authenticated natural-language scan requests select an operator-configured
  repository, capture its exact HEAD and enqueue one independent Security handoff
  through the existing Switch outbox. Replayed Discord messages reuse the scan.
  Multiple configured repositories require an explicit owner/repo selection.
- Contextual status, findings, recorded logs and cancellation resolve only in the
  original task scope. Multiple candidates require an explicit UUID. `/flip`
  aliases retain `/crew` compatibility; signed inspection commands verify Discord
  identity and thread mappings.
- `Fix the first one` and `investigate the first finding` resolve the persisted
  scan ranking, stable finding ID and exact revision. Selection survives restart;
  ambiguous scans require a task ID. Fixing enters the existing human Engineering
  approval gate and requires a matching configured destination repository.
- Native Trivy, Semgrep and pip-audit report adapters provide ranked stable finding
  IDs. Missing or stale dependency databases, skipped analysis and malformed
  reports remain degraded or blocked. Scanner artifacts are optional, bounded and
  redacted. See [scanner setup](SECURITY_SCANNERS.md).
- Company Brain retrieval precedes Research selection. Structured graph symbol
  and file identifiers influence ranking within operator-configured sources.
  Graph excerpts cannot add URLs, commands, approvers or permissions. Checkout
  mutation during retrieval discards the result. Controlled integration proves
  that a revision-grounded caller changes the selected evidence.
- QA and Reviewer subprocess output is bounded and redacted before persistence;
  excess output fails validation. Receipts retain timings, exit/timeout status,
  command argv and private log artifacts. Reviewer Docker repository mounts are
  read-only. Host-process fixture mode remains distinct from OS isolation.
- Guarded merge requires independent persisted QA, Reviewer and mandatory passing
  Security evidence, current remote head and
  base, successful required checks, a ready PR and a separate exact-SHA human
  merge approval. Durable pre-mutation claims prevent a second merge PUT after
  uncertainty. Signed Discord merge controls use the same service and defer
  responses before network work. See [merge setup](GUARDED_MERGE.md).

## Architecture and trust boundaries

Discord bridge bindings or signed interactions establish the human identity;
message text cannot supply it. PM creates durable tasks/handoffs in SQLite and
Switch carries addressed messages to independently provisioned workers. LangGraph
retains existing role workflows. Operator-owned templates authorize managed Codex
Engineering, disposable QA checkouts and independent Reviewer commands. Company
Brain supplies revision-grounded repository evidence. Waggle supplies prior
lessons through the existing opt-in memory/lesson services; it is not a code graph.
Security uses the shared SecurityRunner. Release publishes human-approved draft
PRs, while the API-only guarded merge credential is kept outside untrusted code.

Container execution remains the existing digest-pinned Docker adapter: bounded
resources, no network, read-only root, dropped capabilities and narrow workspace
mounts. It is not a microVM. An unconfigured host-process runner does not provide
filesystem or network containment. Full log download, live terminal streaming,
interactive PTY and universal forced cancellation are not implemented here.

## Acceptance matrix

| Mission scenario | Implemented / automated evidence | New live evidence |
| --- | --- | --- |
| A: full security-to-merge | Scanner intake/parsers, managed pipeline and guarded merge components; continuous organic end-to-end scenario remains incomplete | Blocked: no active configured Docker daemon; native scanner binaries absent on host; Discord/Switch/GitHub workflow not run |
| B: conversation continuity | Scoped logs/findings/status/cancel, ranked finding selection with restart provenance, and scan routing tested | Pending |
| C: regression rejection | Existing real local subprocess QA correction tests; independent review gates retained | Earlier disposable pipeline report only |
| D: stale approval | Changed head/base/check policy rejected; exact SHA merge request tested | Pending disposable GitHub acceptance |
| E: unauthorized actor | Human/service separation and signed Discord scope tests | Pending |
| F: sandbox security | Docker policy tests, read-only review, subprocess timeout/output/cancel checks | Blocked: Colima socket unavailable |
| G: Switch communication | Existing separate identities, durable outbox and worker tests retained | No new messages sent; historical report remains separate |
| H: Company Brain influence | Controlled real-Git + MCP-adapter integration changes research selection before execution; stale/dirty evidence rejected | No current checkout graph sync or live workflow run |
| I: Waggle learning | Existing approval-gated lessons/recall tests retained | Two-task live learning incomplete |
| J: voice input | Existing consented file provider tests retained | Local CPU STT and direct Discord voice-note intake incomplete |
| K: recovery | Existing recovery tests plus scan replay and merge uncertain-response reconciliation | No new live restart run |

Mocked MCP/GitHub responses prove adapter behavior only. Actual local subprocess
and Git tests prove those local operations only. Neither is live external-service
acceptance. Existing reports must remain dated and attributed to their original
repository and revision.

## Reproducible verification

Major changed modules:

| Responsibility | Modules |
| --- | --- |
| Discord intake, task controls and finding provenance | `workers.py`, `conversation.py`, `crew.py`, `discord_interactions.py`, new `security_intake.py` and `finding_selection.py` |
| Scanning and durable scan scheduling | `security.py`, `security_gate.py`, `triggers.py`, `organization_runtime.py` |
| Graph evidence influences investigation | `company_brain.py`, `graphs.py`, `research.py` |
| Bounded independent validation | `qa.py`, `reviewer.py`, `approval.py`, new `execution.py` |
| Human-controlled merge | new `merge.py`, `api.py`, `settings.py`, `.env.example` |

Modules are under `src/switchcrew/`. New regression suites cover execution,
graph-guided research, finding selection, Security intake, conversation controls
and guarded merging. Existing scanner, trigger, gate and Reviewer suites were
extended. The pre-existing demo reproduction script received a line-wrap fix so
the repository-wide Ruff lint command passes.

```sh
uv sync --frozen
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check src tests scripts
uv build
git diff --check
```

Use private operator configuration in [organization setup](ORGANIZATION_SETUP.md),
[Switch provisioning](../deploy/switch/README.md), [Company Brain](COMPANY_BRAIN.md),
[Waggle](MEMORY.md), and [isolation](ENGINEERING_ISOLATION.md). Preserve existing
`.env` and registration files. Required credentials must never be committed.
No new runtime dependencies are required by this increment.
Set `SWITCHCREW_MEMORY_PROJECT=github.com/ria-kayy/Discord-server` for this
checkout; inherited Flip examples/defaults refer to the historical deployment.
Do not reuse another repository's Waggle or Company Brain scope as evidence here.

The baseline suite passed **612 tests, 3 skipped**. An intermediate integrated
suite passed **650 tests, 3 skipped** before the last finding-selection and
detached-process regressions. Final verification passed **661 tests, 3 skipped**
in 65.68 seconds. Ruff lint, format checks (134 files), `git diff --check` and
source/wheel builds passed. Results and changed-file hashes are recorded in
[verification evidence](evidence/harness-2026-11-05.json); remote CI was not invoked. A Docker inventory check
failed because the configured Colima socket was absent, so container/live
acceptance was not attempted. No GitHub branch push, PR creation or merge was
performed in this task.

## Phone demo runbook

These are instructions for a future recording, not recorded outcomes. Provision
independent PM/Research/Security/Engineering/QA/Reviewer/Release identities, exact
Switch addressing rules, a configured private scan route, a current Company Brain
index and a disposable GitHub destination first. Use a fresh task thread.

1. Send `Scan ria-kayy/Discord-server for vulnerabilities and report back.`
   The configured target must be the authorized checkout; the bot should return
   an exact revision and task ID. Record actual Switch message IDs separately.
2. Send `show me the findings`. Record real scanner versions, coverage warnings,
   stable finding IDs and revision. A degraded result must be shown as degraded.
3. For task controls send `what is the status`, `show me the logs`, or
   `/flip findings TASK_UUID`. If several tasks match, select the UUID explicitly.
4. Send `investigate the first finding`, then `fix the first one`. If several
   scans share the thread, use `fix the first finding for task SCAN_TASK_UUID`.
   This resolves the actual finding and creates a separately approved Engineering
   request only for a matching configured repository. The existing explicit
   Engineering format also remains available:

   ```text
   Fix the selected finding in the authorized repository.
   Success criteria:
   - Add a regression check for the selected finding at its recorded source revision.
   - Pass the configured independent QA, Security and Reviewer checks.
   ```

   Review the selected finding and configured job before sending the exact
   versioned Engineering approval phrase returned by PM. Text from findings or
   graph evidence does not approve execution. The operator job must target the
   intended source/base; this is not arbitrary repository provisioning.
5. Show Company Brain provenance, the actual managed Codex patch, QA command
   receipts and Reviewer/Security evidence at the same SHA. Do not represent a
   failed gate or document-only result as verified reproduction.
6. Send the exact `Approve release for task TASK_UUID version VERSION` phrase
   from PM. Verify the resulting draft PR in the disposable repository.
7. An authorized human must mark the draft ready in GitHub. With current checks
   published and configured, use signed `/flip merge-status task_id:TASK_UUID`,
   then `/flip approve-merge task_id:TASK_UUID revision:FULL_SHA version:MERGE_VERSION`,
   then `/flip merge task_id:TASK_UUID revision:FULL_SHA`. Capture the real receipt.
   If the base/head changes, re-run the Engineering validation workflow.
8. Demonstrate `stop the current task` on a separate unambiguous disposable task.
   Cancellation blocks future dispatch but cannot recall an external request
   already in flight. Do not merge to demonstrate cancellation.

## Remaining priorities

1. Run the complete newly integrated flow on a disposable repository with active
   Docker/Switch/Discord and actual scanner tools/databases; verify isolation and
   real expected-SHA merging. CI has not been run remotely for these changes.
2. Complete investigation/reproduction-specific workflows and automatic approved
   revalidation after remote head/base changes. Graph-guided Research is bounded
   source ranking, not full symbol-aware QA test selection or arbitrary PR review.
3. Publish trustworthy GitHub checks and support draft-to-ready authorization
   from Discord; currently GitHub readiness remains an explicit external step.
4. Demonstrate approved Waggle lesson storage changing a second live task.
5. Add local CPU voice transcription and authenticated Discord attachment intake,
   retention cleanup, full log access and rate-limited progress streaming.
6. Rehearse fresh-machine bootstrap and long-lived worker hosting. Keep embedded
   terminal, stronger sandbox backends and multi-repository expansion secondary.
