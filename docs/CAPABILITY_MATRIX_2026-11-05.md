# Flip capability matrix — 2026-11-05

This matrix separates live deployment evidence from local executors and fixtures. A role in the registry is not evidence that its Discord path works. “LIVE” means a real Discord/Switch run is recorded in the linked evidence; “LOCAL” means code or tests exist but this session did not verify that role through Discord; “BLOCKED” means a real prerequisite is missing.

## Real Discord and Switch workflows

| Capability | Status | Evidence and limit |
|---|---|---|
| Discord identity, PM intake, natural-language Research handoff | LIVE | Real Discord messages and Switch handoff in the 2026-11-05 rehearsal; the earlier Gateway research task is `eb714d1c-e95c-5c31-aeb8-fa4a140b05aa`. Natural-language result and Switch references are documented in [NATURAL_LANGUAGE_2026-11-05.md](NATURAL_LANGUAGE_2026-11-05.md). |
| PM → Research on Flip implementation boundaries | LIVE | Natural-language request submitted in `#flip-test`; actual Discord capture is in the private rehearsal footage. Research was asked to cite files and distinguish implemented controls from limits. The browser recording tool became unavailable before a longer follow-up sequence could be captured. |
| Console-managed Codex Engineering execution | LIVE (disposable repo) | Independent task `c3994e63-416a-5791-b57d-43ea5c02f80e`; explicit human approval; actual correction loop; final revision `128b821bd467bd4031a6c765cef3fb8b571cfc34`. |
| QA failure and correction gate | LIVE (disposable repo) | QA rejected the first revision, sent the correction through Switch, then passed both configured tests on the corrected revision. See [LIVE_PIPELINE_2026-11-05.md](LIVE_PIPELINE_2026-11-05.md). |
| Independent Reviewer and security scan | LIVE (pipeline gates) | Reviewer accepted the corrected exact revision; an independent secret scan passed at that SHA. This does not establish that a standalone Security Discord conversation was demonstrated. |
| Human release approval and GitHub | LIVE (disposable repo) | Separate human release approval created real draft [PR #1](https://github.com/Abhigyan-Shekhar/flip-acceptance-20261104/pull/1) at the verified SHA. This PR is only for the disposable acceptance repository. |
| QA and review rerun / stale evidence controls | LIVE (acceptance workflow) | The workflow used commit-bound QA and reviewer results. See the task evidence JSON and report; do not generalize this to live VC, Company Brain, or other capabilities. |
| Console → Switch communication in plain language | LIVE (natural-language worker display) | Worker handoffs and results display readable prose with durable reference IDs; the authoritative payload remains in the local durable store. The console execution brief remains private. |

## Registered roles and current verification

The source registry contains 17 roles (`src/switchcrew/agents.py`). The list below classifies each executor by evidence, rather than treating registration as proof.

| Role | Source capability | Status | Verification boundary |
|---|---|---|---|
| PM | `research_planning` | LIVE, bounded | Real Discord intake and Research routing verified. A complete natural-language parallel orchestration with every specialist was not captured in this run. |
| Research | `source_retrieval` | LIVE, bounded | Real Switch handoffs and source-backed reports verified. Each report is limited to its configured sources. |
| Engineering | `switch_codex_session` | LIVE, disposable repo | Real Console-managed Codex assignment and isolated execution verified on the acceptance task. The urllib3 issue remains unimplemented pending its own Discord request and engineering approval. |
| QA | `isolated_qa_runner` | LIVE, disposable repo | Real failing and passing test gates verified on the acceptance task. A separate Docker reproduction of urllib3 issue #5181 is LOCAL and currently fails on baseline as expected. |
| Reviewer | `independent_diff_review` | LIVE, disposable repo | Commit-bound review verified on the acceptance task. |
| Release | `authorized_publication` | LIVE, disposable repo | Human-approved draft PR creation verified for the acceptance task. No upstream urllib3 PR was created. |
| Security | `security_analysis` | LOCAL / pipeline gate | Independent secret scan ran in the live acceptance gate; a standalone Security specialist conversation was not tested. Scanner findings are bounded by configured policy. |
| Documentation | `documentation_writer` | LOCAL | Snapshot-based draft executor exists. No real Discord handoff was run in this rehearsal. |
| Knowledge | `knowledge_curation` | LOCAL | Snapshot-backed executor exists. Verified lesson storage and a changed outcome on a second live task were not demonstrated. |
| Community | `support_triage` | LOCAL | Snapshot-backed support analysis exists; no live Discord run recorded. |
| Onboarding | `onboarding_guide` | LOCAL | Snapshot-backed draft exists; no live Discord run recorded. |
| Moderation | `moderation_review` | LOCAL | Snapshot-backed review exists; no live Discord run recorded. |
| Analytics | `metrics_access` | LOCAL | Requires a bounded metrics snapshot; no live Discord run recorded. |
| Scheduling | `calendar_access` | LOCAL | Requires an explicit calendar snapshot; no live calendar integration or live Discord run recorded. |
| Incident | `incident_triage` | LOCAL | Deterministic incident planner exists; live incident intake and specialist handoffs were not run. |
| Voice | `consented_voice_io` | BLOCKED | No consented recording or configured speech-provider credentials were available. No recording was transcribed. |
| Notes | `evidence_linked_meeting_notes` | BLOCKED | Same missing recording/provider prerequisite. No claims of live VC, DAVE, connectivity monitoring, or interactive clarification. |

## Verification references

- [Live approval-gated pipeline report](LIVE_PIPELINE_2026-11-05.md)
- [Natural-language Switch communications report](NATURAL_LANGUAGE_2026-11-05.md)
- [Company Brain verification](COMPANY_BRAIN_2026-11-05.md)
- [Recorded-meeting acceptance notes](RECORDED_MEETING_ACCEPTANCE.md)
- Registry and role executors: `src/switchcrew/agents.py`, `src/switchcrew/specialists.py`, `src/switchcrew/graphs.py`

## This rehearsal's remaining gaps

- The open Codex browser became unavailable: `cua.getState()` reported “Local browser use is disabled in Work settings” and returned no browser tabs, even after the user re-enabled access. Per the mission instructions, no credentials or cookies were extracted and no substitute Discord session was created.
- The longer Discord walkthrough and additional real footage are therefore incomplete. Existing source footage in the private demo directory is authentic but covers only the captured PM/Research messages.
- The urllib3 issue #5181 baseline reproduction is real and isolated, but no Engineering change was requested or approved for that repository during this rehearsal. No upstream code or PR was changed.
- A consented recording and speech provider are still required for Voice/Notes acceptance.
