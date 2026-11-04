# Verification checkpoint — November 4, 2026 IST

The local Discord research workflow is deployed and verified. The complete
coding-to-release and live voice workflows are **not yet accepted**.
This checkpoint supersedes earlier statements that Docker or Switch were
unavailable on this Mac; those dated reports remain as historical evidence.

Runtime changes are merged through `7caa1a415025968d8bbf2d694ea47a0a4b1608d3`.
The latest implementation fix is [PR #10](https://github.com/Abhigyan-Shekhar/flip/pull/10).
The 556-test run covered its implementation before merge; this documentation
update does not claim a new implementation test run.

## What is built

The existing Python 3.12/FastAPI application uses SQLite task history, durable
inbox/outbox processing and LangGraph specialist workflows. Switch carries real
addressed handoffs between independent agents; Discord is the human interface.

Implemented capabilities include bounded PM planning and configured specialists;
Engineering approval, independent QA/Reviewer and bounded corrections; human
Release approval and draft-PR support; optional restricted container execution;
recorded Voice/Notes and confirmed action proposals; knowledge and lesson
lifecycles; Waggle and Company Brain adapters; incident/OSS intake; change
reports; employee bindings; watchdog monitoring; and the operator dashboard.

All 17 roles have graph implementations. This checkpoint deploys **PM and
Research**, not a fully provisioned 17-agent organization. Implemented code,
controlled tests, component acceptance and complete live workflows are distinct.

## Results and boundaries

| Area | Observed result | Boundary |
| --- | --- | --- |
| Automated suite | **556 passed, 3 skipped**, 31.04 seconds | The skipped tests are opt-in real Switch recovery probes. |
| Real Switch recovery | **3 passed**, 40.46 seconds, in a separate run | Dedicated recovery-probe identity on the pinned local server; not acceptance of every privileged worker. |
| Static/package checks | Ruff lint/format, diff check, Compose validation, wheel and source build passed | A successful build does not provision external accounts. |
| Local infrastructure | Colima/Docker, PostgreSQL, Switch, gateway and app API healthy | Loopback-only local setup; not a public production deployment. |
| Core host preflight | Runtime, service health, worker authentication and required Discord fields passed | `deploy/switch/preflight.py` is narrower than the full privileged-workflow preflight. |
| Local API | 15 actual HTTP checks passed, including auth denial and persistence after process restart | Disposable local database and locally signed synthetic Discord requests in that initial probe. |
| Codex harness | Actual authenticated CLI requests produced PM plans and Research drafts | Read-only harness execution is not a Console-managed Engineering session. |
| Discord text workflow | Actual Discord request, PM plan, addressed Research handoff, acknowledgment and cited reply in the original thread | Draft awaits human review; no privileged execution or release was authorized. |
| Discord restart and identity denial | Task/handoff state retained; no repeated thread replies; bot-authored user-ID claim created no task | This negative test covers bot-origin identity spoofing, not every possible unauthorized account. |
| Native `/crew status` | Real Discord-signed request returned the correct task status after the thread-scope fix | Temporary HTTP test; original routing was restored and `/crew` removed afterward. |
| Container isolation | Root/workspace writes denied, networking disabled, no service secrets inherited, timeout cleanup left no orphan container | Runner boundary test, not proof of complete host/VM containment or Security scanner coverage. |
| QA/Reviewer components | Broken commit rejected; corrected exact SHA accepted; changed HEAD invalidated cached evidence | Operator-prepared synthetic Git artifacts and execution ledger, with actual isolated subprocesses. |
| Waggle | Real cross-role observation persisted and a fresh client recalled it; deployed Research outcome also persisted | Dedicated acceptance scopes; not proof of causal learning on a second real assignment. |
| Two-task learning experiment | Failing baseline and passing post-retrieval regression | Controlled memory/approval/QA fixtures; `live_waggle_called=false`. |

## Live Discord evidence

The temporary bot was authenticated against the supplied application and public
key. Guild, test channel and requesting member were verified through Discord.
The real built-in Switch Discord bridge was connected, the owner identity was
linked, and transport sender mappings were checked against server records.
The application uses explicit guild/user allowlists and a verified requester
binding. Agent addressing is restricted to the linked owner and peers in the
intended rooms.

An authorized read-only request was posted through the user's logged-in Discord
desktop app, rather than injected into Switch or SQLite:

1. Discord delivered the request to PM.
2. The actual Codex PM harness produced a bounded Research plan.
3. PM delivered the Research assignment through Switch.
4. The independent Research worker explicitly acknowledged it.
5. The Research harness returned a cited draft, and its Waggle outcome persisted.
6. PM delivered the result back to the original Discord thread.

| Receipt | Identifier |
| --- | --- |
| Parent task | `2417f12d-ba19-53b0-862e-53f37875c78d` |
| Research task | `38936562-528d-5981-8705-182293b06b33` |
| Research handoff | `0dbb0630-dca4-501b-8416-71c1e81cd39e` |
| Original thread | [Discord thread](https://discord.com/channels/1565566745622872115/1567445445662220371) |
| Result reply | [Discord result](https://discord.com/channels/1565566745622872115/1567445445662220371/1567445671907430461) |
| Successful slash interaction | `1567449930648395876` |

The Research result is complete, but the orchestration step is `awaiting_review`
and the parent remains `IN_PROGRESS`. No human decision was impersonated or
submitted automatically. A model draft is not an accepted task or a release.
After restarting API/PM/Research, task states and handoffs were unchanged and
no original-thread reply was duplicated.

Discord accepted the HTTP endpoint's signed PING and received HTTP 401 for its
invalid-signature probe. The first real `/crew status` attempt failed with 401;
after the verified mapping fix the user retried it and confirmed:

> Task 2417f12d-ba19-53b0-862e-53f37875c78d: IN_PROGRESS (version 2) Workflow: running

The temporary tunnel forwarded only `/discord/interactions`, not the dashboard
or task APIs. After acceptance, the original application endpoint was restored,
the temporary command removed, and proxy/tunnel stopped. The Discord text bridge
remains connected. Persistent custom slash hosting is still a deployment task.

## Findings and fixes

| Finding | Resolution | Status |
| --- | --- | --- |
| Terminal approval workflows inflated dashboard counts and could hide pending work behind the row limit. | Filter terminal workflows/tasks before applying the limit; add regression coverage. | Merged [PR #8](https://github.com/Abhigyan-Shekhar/flip/pull/8). |
| Malformed signed Discord payload shapes could cause server errors. | Validate member/user/data/options shapes and return client errors. | Merged PR #8. |
| Real Research output supplied evidence objects where the schema requires strings. | State citation types and existing field bounds explicitly; keep validation and human review. | Merged [PR #9](https://github.com/Abhigyan-Shekhar/flip/pull/9); fresh real draft succeeded. |
| Signed thread commands used a Discord thread channel ID against a parent-channel binding and an opaque Switch root. | Add separate operator-verified thread mappings, preserving user/room/task scope. | Merged PR #10; real retry passed and seven regression cases cover scope denials. |
| Compose API omitted the Discord settings required by the HTTP handler. | Forward public key, allowlists and identity/thread bindings. | Merged PR #10. |
| Editable Python installation failed outside pytest's source path on this Mac. | Use the documented non-editable install and retain it with `uv run --no-sync`. | Installed imports, acceptance modules and dashboard template passed. |
| Docker cleanup's minimal environment could not find Homebrew Docker or its current context. | Configure the absolute binary and explicit Colima socket. | Real cleanup/timeout test passed. |
| macOS private temporary directory was not shared with the VM. | Place disposable execution checkouts under the shared home directory. | Real QA/review container runs passed. |
| A custom HTTP interactions endpoint takes delivery away from the same bot's built-in Switch slash commands. | Restore the original endpoint after testing; use a separate app or complete dispatcher for persistent custom commands. | Test routing cleaned up; persistent deployment remains pending. |

## Exact-SHA QA and review component evidence

The operator created the test commits; no model implementation or human approval
is claimed. The actual `QAExecutor` and `ReviewerExecutor` each ran commands in
fresh restricted containers using:

`python@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30`

| Artifact | SHA | Outcomes |
| --- | --- | --- |
| Broken implementation | `f645256fa6f28d7c714bd7655e88ef2ed68e78a1` | `qa_failed`, `review_rejected` |
| Corrected implementation | `5b11d242a24e8d5f74be602342a66596acf1414e` | `verified_success`, `review_accepted` at this SHA |
| Later changed HEAD | `50977c4325c5691446f04b15e66c25ac69079362` | Previously selected evidence returned `stale_qa`, `stale_review` |

The review command checked the explicit sample regression. It does not certify
general code quality or Security coverage. The temporary source checkouts were
removed after the test; sanitized outcome/revision summaries are retained.

## Remaining acceptance gates, in order

1. **Managed coding and release:** onboard a dedicated Console-managed Codex
   agent at a disposable assignment checkout; configure independent Engineering,
   QA, Reviewer, Security and Release workers, meaningful pinned commands/scanners,
   a scoped GitHub destination/credential and branch policy. Obtain actual human
   execution and Release approvals. Demonstrate failure, correction, fresh checks,
   a real draft PR and an evidence-backed Discord receipt. The latest Console
   control probe returned HTTP 200 with **zero managed agents**.
2. **Recorded audio:** configure the speech provider and private Voice/Notes jobs,
   use a real recording with current revocable participant consent, and verify
   provider, transcript, notes and confirmed-task receipts. No ElevenLabs key or
   consented real-audio job was available in this run.
3. **Live voice:** complete and verify DAVE-compatible receive/transmit, speaker
   identity, membership changes, revocation/stop and reconnect behavior. Recorded
   file processing does not establish this capability.
4. **Graph and learning:** register/index this exact repository revision in
   Company Brain, verify anchored graph retrieval, then demonstrate an approved
   lesson changing behavior on a second real deployed task. Controlled two-task
   fixtures and generic Waggle recall are insufficient for that claim.
5. **Persistent slash deployment:** choose a separate application or a dispatcher
   for all bot commands, maintain verified thread mappings, and retain stable
   HTTPS hosting. The temporary successful test is not a permanent deployment.
6. **Reproduction and demo:** repeat the selected workflow from documented setup,
   collect exact revisions/IDs/approvals and rehearse a continuous demo. Keep the
   pre-event/event-day build history accurate.

## Evidence retention

[Sanitized machine-readable results](evidence/verification-2026-11-04.json) contain
statuses, artifact SHAs and public receipt identifiers. Private credentials, raw
callback tokens, full configurations, databases and provider material are not
committed. Local evidence remains under ignored `.switch/runtime/`, including:

- `host-preflight-after-discord.json`, `discord-live-evidence.json`;
- `discord-restart-negative-evidence.json`, `interactions-metadata.jsonl`;
- `real-isolation-evidence.json`, `real-qa-review-component-evidence.json`;
- `remaining-prerequisites.json`, `slash-test-cleanup.json`, private runtime logs.

See [the Mac runtime runbook](LOCAL_MAC_RUNTIME.md) for lifecycle and access.
These files belong to this provisioned Mac; a fresh clone does not contain them.

## Timing integrity

November 4 setup, live verification and PR #8–#10 fixes are event-day work.
The broader implementation began before the event; this checkpoint does not
relabel all existing functionality as event-built or establish eligibility.
Earlier dated build logs and evidence are preserved.
