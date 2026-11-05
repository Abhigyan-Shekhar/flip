# Live coding-to-draft-PR acceptance — November 5, 2026 IST

Follow-up: [readable conversation and plain-text requests](NATURAL_LANGUAGE_2026-11-05.md)
are now deployed. Their live Research check is separate from this historical
coding-to-PR run; the complete coding pipeline has not been rerun with that display.

**The complete disposable coding workflow passed live.** A human submitted the
request and both approval stages in Discord; real Switch handoffs drove two
Console-managed Codex assignments, independent QA and review, an independent
Security gate, and a real GitHub draft PR. No mocked service responses were used.

- [Draft PR #1](https://github.com/Abhigyan-Shekhar/flip-acceptance-20261104/pull/1)
- [Discord release receipt](https://discord.com/channels/1565566745622872115/1567582249719701555/1567716505473261640)
- [Sanitized evidence manifest](evidence/live-pipeline-2026-11-05.json)

This started from fetched Flip master `5bd88bdc803cc5d30d361923b3dfce62e93bfe65`.
Application integration fixes were in the local `codex/live-pipeline-acceptance`
working tree during execution; source hashes are in the manifest. The disposable
PR contains only the answer-file fixture, not these application changes.
Existing agents/infrastructure and the November 3 approval/review implementation
predate the November 4–7 hackathon. November 4–5 work is deployment, integration
repair and live verification. The [November 4 preparation checkpoint](LIVE_PIPELINE_2026-11-04.md)
is retained as historical evidence, including its then-unresolved blockers.

## Observed sequence

Task: `c3994e63-416a-5791-b57d-43ea5c02f80e`. Room:
`1d8c9f05-5e7e-41b9-aead-bfd1b10b927d`. Destination:
`Abhigyan-Shekhar/flip-acceptance-20261104`, base `master`.

The human deliberately requested a first failing revision to exercise the
correction gate. This is a small answer-file fixture, not a general code-review
benchmark. PM selected the operator-owned job template and delegated through
Switch. It did not demonstrate open-ended decomposition of a complex feature.

| Step | Observed evidence |
| --- | --- |
| Human Engineering approval | Discord actor `703216309684666389`, version 1, `2026-11-04T16:58:23.491562+00:00` |
| First Engineering handoff | `e285ca4e-d105-5134-a985-4b8ee2aefaba` |
| First assignment / Console session | `3fd02cf557ff66ac13d8cc03e15c2606` / `64bb4f4f-1ea8-49b2-9b51-c86c4942a799` |
| First implementation | `b35e016579843d3a73d21a910248f2ffb346d05b`: intentional failure |
| Independent QA rejection | `qa-90e1691b49d0fe32cc7c0e8204771bd00aa6133e6592b0b06d18b63b4877e27b`; 2 tests, 1 assertion failure, exit 1 |
| QA → Engineering correction | `engineering-74f3c25d550e9bdacf39080c794ed5a6bc6ad61fdfd987ef2b6d05b4107142fb` |
| Correction assignment / Console session | `d490c7b875451e4d935dccd2e97fbf99` / `0eef8f63-5157-4fb1-abb5-9dbbfd8a4d56` |
| Final implementation | `128b821bd467bd4031a6c765cef3fb8b571cfc34` |
| Fresh independent QA | `qa-f1b8541f1c00583b50aa0bf7b7aa0fcef51888edfb33c3367920eea0e4c32835`; 2 tests passed, exit 0 |
| Independent Reviewer | `review-f1b8541f1c00583b50aa0bf7b7aa0fcef51888edfb33c3367920eea0e4c32835`; exact final SHA, accepted, no findings |
| Final Security scan | Task `49f2c868-82c7-51df-898d-2ae94191e022`, handoff `e9eafcbe-af3e-510f-af7b-daf7668214f3`; exact final SHA, verified success |
| Human Release approval | Same authenticated human, separate stage, version 13, `2026-11-05T01:45:35.144270+00:00` |
| Release handoff / execution | `9af8cc75-3ece-5ab4-865c-e3352a89d879` / `release-64eaec944e59036011b3ef6b23547b47` |
| GitHub / Discord | Draft PR #1; receipt message `1567716505473261640` at `2026-11-05T01:45:41.669000+00:00` |

GitHub was independently read back: OPEN, draft=true, head exactly the final SHA,
base master, only `answer.txt` changed. Cumulative baseline-to-final change is
`broken\n` → `correct\n`; README stayed unchanged. QA tested exact bytes and
regular-file status. The separate Reviewer process checked exact bytes, README's
operator-pinned hash and cumulative diff integrity in a pinned container.
Reviewer independence here is a separate identity/process and operator-owned
deterministic review policy; it is not a second model's semantic review.

Discord's API confirmed the receipt in the original request's thread, including
PR URL, revision, both human approvals and actual test command/exit status.
The durable release receipt additionally records assignments, handoffs, review,
test-output hashes and correction history in the manifest. Discord's displayed
receipt does not currently include all those execution/session IDs.

## Console provisioning and actual isolation

Two real official controller-v2 Codex agents were provisioned, not CLI stand-ins:

| Attempt | Managed agent | Controller |
| --- | --- | --- |
| Initial | `61417b1c-0197-4cb7-8b94-54a4caaa413e` | `8d030ed5-73b1-4cf6-95b7-88765e7adef0` |
| Correction | `f27a059c-110e-46fd-b089-2fe3d6956b20` | `ef7bd77f-95b3-4a33-9816-13457bc4d3e6` |

Used the installed official Console controller/shared-host bundles, controller
protocol 2, and Codex 0.166.0. The new opt-in `ManagedConsoleCodex` adapter checks
real controller assignment and watcher health, pins definition revision, binds
one agent/container to one approved checkout, and calls the official shared-host
control protocol to warm/adopt the actual session. Execution still arrives through
Switch. `auto_approve=false`; no permission-bypass launch flags were used.

The controller, watcher and provider ran inside Colima-hosted Docker containment:
nonroot UID 501, read-only root, capabilities dropped, no-new-privileges, resource
limits, no published ports, no host Docker socket/home, exact checkout/state/
isolated-provider-home mounts only. The agent had only an internal Docker
network with DNS disabled; a separate proxy allowed OpenAI HTTPS endpoints and a
fixed Switch relay. Real probes denied arbitrary external access, host credentials
and socket access. The task checkout was writable; QA/Reviewer/Security used
separate read-only, network-disabled container workspaces.

Correction image: `sha256:c4e7d32202230963d95db820eaefc88f1a69fc9a3d89dd9ba242dee49f9087a9`.
The first image lacked `ps`; its narrowly scoped private runtime repair and real
controller restart preserved the original session/delivery. The corrected image
includes `procps`. No direct Console database writes were used.

Credentials/configs stayed outside Git under a mode-700 private runtime; individual
credential files are mode 600. Only Release received a fine-grained GitHub token:
one disposable repository, Contents/Pull requests read/write, Metadata read-only,
expiry November 11. Engineering never received the broad setup OAuth credential
or the Release credential.

## Failures diagnosed and changes made

- Console first-run access required the human to unlock the Mac. Connected the
  existing server, enabled its managed-controller feature and private signing key,
  and enrolled actual controllers. The old desktop control API did not enumerate
  daemon-managed agents; added the controller-v2 integration rather than faking one.
- Registration UUIDs differ from Switch message-sender transport IDs. Corrected
  private registries and PM receipt mappings against authoritative server records.
- A handoff sent while a worker was offline was missed on startup. Base runtime
  now recovers actual history once on startup, including with an existing cursor;
  durable message/assignment IDs continue to prevent repeated effects.
- QA/Reviewer acknowledgments need reciprocal addressing permission. Corrected
  private registries and live Switch rules: Engineering accepts PM/QA/Reviewer;
  QA accepts Engineering/Reviewer; Reviewer accepts QA/Engineering; Release accepts PM.
- Official provider startup failed because the image omitted `ps`; added procps.
  The initial claimed session was restarted and resumed, not replaced or re-prompted.
- Watcher health placements are session→room mappings. Validate mapping values.
  Provisioning now waits for actual controller/provider readiness before placement.
- Native Trivy report shapes differed from the adapter assumptions; fixed clean
  and finding envelopes while preserving fail-closed handling of malformed reports.
- A real truncated SSE takeover opened another stream. Added ownership validation
  after transport errors; all three real recovery probes subsequently passed.

Security used real Trivy 0.76.1, pinned image
`aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`.
The persisted High-or-above policy rejected a nonfunctional Critical secret fixture;
clean scans passed. The live release required its own exact-SHA scan. This is
secret scanning only; vulnerability database and publisher-signature acceptance
are unverified. The stored `security_gates.status` display can remain pending;
authorization derives from the actual scan report and current policy, not that
denormalized field. This display inconsistency remains open.

## Failure, replay and restart checks

The QA failure generated one bounded correction with a new immutable assignment;
the original assignment/session was not executed again. After publication,
restarted Engineering, QA, Reviewer, Release and PM. After reconnect/history
recovery, the execution ledger's six rows and full digest were unchanged, task
stayed PR_OPEN/version 17, GitHub had one matching PR, and Discord one release
receipt. This covers completed-publication restart, not a crash at every possible
point during an uncertain GitHub POST.

Human rejection probe `bc2b9594-1ca6-4d0b-973f-33b263e57e05` was explicitly an
operator-created API fixture, not a second human coding request. The human sent
two valid rejection messages in Discord. The first cancelled it at version 2;
the repeated stale version did not create a handoff or execution. After restarting
PM and Engineering again, it remained CANCELLED/version 2 with zero handoffs and
zero execution rows for that task; the global ledger remained at six rows. Two earlier
messages had trailing periods and were invalid JSON: neither authorized anything,
but PM produced confusing generic clarification tasks. Added explicit
invalid-approval feedback without repairing JSON or creating a new task; a controlled regression verifies this fix. The historical clarification
tasks were retained. Service-token approval denial/cancellation/restart was separately
verified in the November 4 checkpoint.

## Validation and remaining work

Ordinary suite: **592 passed, 3 skipped** (53.52 seconds). The three opt-in real
Switch recovery tests separately **passed 3/3** (40.62 seconds). Ruff lint,
formatting for `src tests` and `git diff --check` passed. A repository-wide
format check additionally found five pre-existing formatting issues in unrelated
documentation/research files; those were left unchanged. Mocked/unit results support the adapter
regressions; only the real receipts above establish this live acceptance.

The current host can demonstrate the core pipeline, with trusted operator
provisioning for each approved attempt. Fresh-machine bootstrap and persistent
process hosting are not yet accepted. The private host scripts are not a portable
installer. GitHub required contexts `flip/qa` and `flip/security` have no check
publisher; independent local evidence does not make this PR merge-ready.
No merge or production deployment was attempted.

The user deferred recorded-audio acceptance until a human provides the recording,
consent and provider setup. Still unrun: real consented audio with a configured
speech provider; live VC,
DAVE and connectivity acceptance; human-approved verified lesson storage and a
second-task learning demonstration; Company Brain use in the PM workflow (the separate
[real revision-grounding probe](COMPANY_BRAIN_2026-11-05.md) now passes); persistent `/crew` hosting and dashboard acceptance. Conversational Waggle
memory is not evidence of the two-task learning workflow. The deliberately
requested QA failure here also does not demonstrate an organic Engineering
mistake or establish that memory changed a second task’s outcome. Other repositories'
Company Brain indexes were not used as Flip evidence.
