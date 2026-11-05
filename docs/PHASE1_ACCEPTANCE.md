# Phase 1 acceptance — November 5, 2026

**Partial live acceptance. Phase 1's complete definition of done is not satisfied.**
The real Discord investigation, Switch handoffs, isolated reproduction, Company
Brain retrieval and approval-gated proposal ran. This scanner story has not run
through managed Engineering, independent post-Engineering gates, a workflow PR
or a human-approved merge. No PR URL or merge SHA is claimed for it.

## Source revisions and scope

- Verified starting branch: `codex/security-to-merge`.
- Vulnerable SHA: `c26948e4d4205b45d54a2faad86fb486d78576f3`.
- Local parser-fix commit: `89c103fa25a9fb796b959ca00b88113094352912`.
- Implementation branch: `codex/phase1-green-check`.
- Original origin is `ria-kayy/Discord-server`; Git verified the brief's
  `Abhigyan-Shekhar/flip` baseline is the same commit. Origin was preserved.
- Vulnerable/patched local refs: `refs/demo/phase1-vulnerable` and
  `refs/demo/phase1-patched`. No production branch was reset or regressed.

## Implemented and code-verified

| Capability | Result |
| --- | --- |
| Native Trivy envelope validation | Incomplete and ambiguous envelopes rejected; valid native and generic reports retained |
| Real pre-fix reproduction | Original subprocess regression: **3 failed / 6 passed** |
| Isolated revision comparison | Vulnerable assertion exit **1**; patched assertion exit **0** |
| Restricted scanner regression suite | **34 passed**, exit **0**, network-disabled pinned container |
| Complete automated suite | **684 passed, 3 opt-in live tests skipped**, 41.97 seconds |
| Post-timeout-setting focused checks | **36 passed** |
| Static/build checks | Ruff lint, format check, package build and Git whitespace check passed |
| Investigation intake | One durable task, two genuine independently addressed handoffs, exact source SHA |
| Follow-ups | Authenticated user/room/thread scope, ambiguity refusal, durable context and linked proposal resolution |
| Fix request | Reproduction + graph prerequisites; creates existing ApprovalWorkflow proposal only |
| Engineering destination | Existing job must match investigated checkout, revision and repository |
| Signed approvals | Engineering/Release controls verify signature, human allowlist, original thread, version; Release binds SHA |
| Existing gates | Existing QA/Reviewer/Security/merge regression coverage retained; no gate bypass added |
| Preflight | Read-only dependency/configuration probe; missing live setup remains BLOCKED |

Evidence: [original regression](evidence/phase1/vulnerable-pytest.txt),
[patched regression](evidence/phase1/patched-pytest.txt),
[container comparison](evidence/phase1/isolated-comparison.json),
[full test output](evidence/phase1/full-tests.txt).

The restricted runner image was actually built from the frozen dependency lock:
`flip-phase1-runner@sha256:e32c311502c9580a3c73ee45b0af79a81c94e6d508d5d121163cbc40e98f1105`.
Execution used disabled networking, read-only root and source mount, dropped
capabilities, no-new-privileges, CPU/memory/PID limits and bounded output/time.
The original reproduction is a scanner-output **contract probe** using real
SecurityRunner code, not a vulnerability-database acceptance scan.

## Externally verified live

[Discord thread](https://discord.com/channels/1565566745622872115/1567863110310559786).
[Sanitized manifest](evidence/phase1/live-investigation.json).

- Investigation: `3dc20e53-2fc3-51b0-9660-a358b7a44592`, **COMPLETED**.
- PM → Security handoff: `2db43b5f-0370-596c-9c3d-d26959f4bd23`.
- PM → Knowledge handoff: `dc211cc2-c32b-5d08-96b8-77ddc223bb6b`.
- Engineering proposal: `e9f51d66-3f50-5203-9cd8-c27befe4214c`,
  **AWAITING_APPROVAL**, task version **1**, **zero Engineering handoffs**.

A real browser message reached PM through the existing authenticated Discord
bridge. Separate Security and Knowledge processes received addressed Switch
messages under their own registrations, accepted work and posted results.
Security reproduced `verified_success` for incomplete native output inside the
restricted container. The assertion exited **1** in 0.4534 seconds.

The actual Company Brain operator CLI indexed the clean vulnerable checkout:
224 files, 1,677 symbols, 2,449 calls, 606 statically resolved imports and
12,009 unresolved calls. It used a separate private registry and real loopback
FalkorDB Lite instance; existing registries/databases were preserved.

Real MCP `check_change` returned the exact-revision
`SecurityRunner.run → _extract_findings` relationship. Byte-checked `read_code`
citations grounded `security.py:210–253` and `security_gate.py:314–365`.
Knowledge recorded the actual engineering decision:

> Run tests/test_security.py and tests/test_security_gate.py: graph evidence connects SecurityRunner to the release security gate.

That decision and its graph snapshot were attached to the real Engineering
proposal's acceptance criteria/spec. This verifies influence on the **proposal's
test selection**; managed Engineering has not yet executed that proposal.
Historical file associations and missing call edges remain explicitly unknown.

After restarting the workers, the investigation and evidence remained intact.
The browser follow-up retrieved the saved receipt. Repeating “Fix it” reused the
same proposal. A real unsigned chat approval was refused and created no
Engineering handoff. No agent supplied or impersonated a signed human approval.
A phone-style curly apostrophe initially fell into generic clarification; it
was fixed, covered by a regression test and successfully retried live. The
historical failed response remains in the thread.

A later real Trivy 0.76.1 secret-only scan of the patched repository returned
**degraded_coverage**, zero findings: the report included dependency metadata
whose database freshness was unverified. This was an operator diagnostic, not
an independent workflow Security gate, and is not reported as passing Security.

## Remaining live blockers

1. **Signed command deployment:** the current Discord application's command
   picker has no `/flip` command. Dedicated interaction hosting/registration and
   verified thread mapping remain unconfigured. Rechecked after the user's
   “try now”; task state and zero Engineering handoffs were unchanged.
2. **Managed Engineering:** the new `green-check` job needs its exact-baseline
   template and verified per-assignment Console provisioning. Existing historical
   answer-file/urllib3 templates do not establish this setup.
3. **Independent gates:** this proposal has not produced an Engineering SHA for
   QA, Reviewer and post-fix Security. Real scanner scope/database freshness must
   meet the configured policy; degraded coverage must keep release blocked.
4. **GitHub:** the disposable remote target, required check publishers, private
   Release/merge credentials and current GitHub checks have not been accepted
   for this story. There is no workflow PR, publication approval or merge receipt.
5. **Human approvals:** actual signed Engineering, publication and merge decisions
   are still required by the supplied brief. Autonomous testing cannot invent them.

The first preflight graph lookup timed out at the adapter's former 15-second
limit. The production Settings adapter now uses a bounded configurable timeout
(default 45 seconds), and a real retry returned revision-matching graph context.
No fallback graph was substituted. Preflight still reported BLOCKED on the
unconfigured live dependencies above.

## Not implemented / limits

- Draft-to-ready through Discord; currently an explicit GitHub human action is
  needed. Therefore the final publication-to-merge experience is not entirely
  Discord-only yet.
- Continuous command streaming or interactive terminal; displays are bounded
  recorded receipts. Running reproduction exposes task/handoff state, then its
  completed command receipt.
- A fresh-machine, one-command provisioning installer. The tracked configuration
  guide and frozen test-runner image are reproducible building blocks; the live
  Mac setup scripts remain private host-specific artifacts.
- A complete three-minute **successful merge** recording. The delivered video
  is clearly labeled a partial acceptance preview and shows genuine captured
  browser frames plus real before/after evidence.

See [implementation](PHASE1_GREEN_CHECK.md) and
[operator/demo steps](PHASE1_DEMO_RUNBOOK.md). Never present the historical
answer-file PR, controlled service adapters, or this local patch as a completed
managed scanner-fix workflow.

## Files changed

The parser fix and its regressions are in `security.py`, `test_security.py` and
`test_phase1_trivy.py`. The investigation integration adds `green_check.py` and
updates `workers.py`, `organization_runtime.py`, `graphs.py`, `wire.py`,
`conversation.py` and `crew.py`. `company_brain.py` adds bounded source citations;
`settings.py` adds the bounded MCP timeout. `engineering_qa.py` binds the new job
to the investigated revision. `discord_interactions.py` adds signed decisions.
`test_green_check.py` covers the new intent, evidence, scope, replay and authority
boundaries. `phase1_preflight.py`, `scripts/phase1_compare.py` and
`deploy/phase1-runner/` provide readiness/reproduction tooling. README, these
three Phase 1 documents and `docs/evidence/phase1/` retain the reviewable record.
