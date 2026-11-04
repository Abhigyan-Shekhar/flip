# Integrated candidate and local evidence — November 3, 2026

## Latest checkpoint — November 4, 2026

[Current verification report](../../docs/VERIFICATION_2026-11-04.md): Colima/Docker,
Switch, gateway, API and PM/Research now run on this Mac. Real Discord Research,
restart persistence and temporary signed `/crew status` acceptance passed.
PRs #8–#10 are merged; latest suite **556 passed, 3 skipped**, with **3 live
Switch recovery tests passed separately**. QA/Reviewer container components were
checked against operator-prepared revisions. Full managed coding/release and
voice acceptance remain pending. See [Mac lifecycle](../../docs/LOCAL_MAC_RUNTIME.md).

The records below are historical checkpoints; retain their failures and limits
as evidence rather than reading earlier host outages as the present state.

The onboarding fix `6b93d2b` and historical recovery-evidence branch are merged
into the integration candidate on `master`. New recovery, configured specialist,
Voice and PM orchestration changes are described in
[the current status](../../docs/DAY1_STATUS.md). Controlled verification passes
258 tests with 3 live tests skipped; real SIGKILL tests cover transaction rollback
and durable pending handoff recovery.

The real bundled Waggle MCP path separately passed cross-role persisted recall
at `2026-11-02T06:17:50.419531+00:00`; see
[the scoped evidence](../../docs/MEMORY.md#live-shared-memory-acceptance).
No new Switch/Discord/Console/GitHub or paid Voice run was performed on this
host. The older strict takeover failure remains an open **live** gate until the
new EOF/heartbeat fix passes the original real-server probe. Historical passes
and failures below are preserved rather than inferred from controlled tests.

---

# Console connection and onboarding integration — November 1, 2026

Observation timestamp: `2026-11-01T13:59:46.938729+00:00` (UTC). Checkout before this fix:
`7c94889`; application base `220fa91` plus the focused onboarding fix in this commit.

- PASS: Console is signed in to existing `Verification Docker Switch` at
  `http://127.0.0.1:8080`; its existing localhost-managed server was preserved.
- PASS: dedicated real Console agent `approval-test-codex-220fa91`, provider
  `codex` (UI reports signed in), Console ID
  `ce9ebb1e-3400-4001-809d-6020e52eb4cb`, Switch ID
  `71cc8b11-052b-422d-a471-88936db0aee9`, location
  `9bbfb227-3e92-4570-858a-1231968c6e73`. The actual adapter validated the
  exact prepared assignment directory. Its session list was empty. No managed
  implementation session or delivery is claimed by this preparation evidence.
- Agent added only to isolated acceptance room; its addressing policy allows
  only that room's Engineering identity. Permission bypass remains off.
- FAIL: first approved task `55d2b9c6-fcc3-5457-adac-ef3535a1aef6` completed
  Engineering handoff `f5230d6d-2c51-577a-8d29-9f6d25f014da` as
  `blocked_configuration` / `checkout_not_clean_at_base`, before session
  creation. Console onboarding generated `.switch/config/<agent>.json` in
  the prepared checkout. The existing implementation excluded only the Claude
  onboarding file and receipt. Task is `FAILED`, version 5; ledger remains
  `reserved`, with no session/delivery/result. No task or approval state was reset.
- FIX: exclude Console's private `.switch/config/` from local implementation
  status, including already-prepared checkouts, and reject any result that
  commits files from that directory. Controlled tests exercise successful
  onboarding and forced credential-file commit rejection. No real credentials
  were used in those tests or committed to this branch.
- Controlled validation: **191 passed, 3 live tests skipped**; Ruff lint and
  format checks passed; `git diff --check` passed. These are not live Codex/QA
  acceptance evidence.
- A requester retry with trailing punctuation created clarification task
  `10495556-4a0f-575e-9d23-574a6a8f882c`; no privileged handoff resulted.
  A corrected plain JSON request and fresh human approval are needed for a new
  assignment. The first task remains as failure evidence.
- NOT RUN: managed implementation, exact-SHA independent QA, Release approval,
  real draft PR/receipt, remaining denial tasks and live recovery scenarios.
  Narrowly scoped Release token remains unconfigured; Release remains stopped.

# Approval/Release review remediation — November 1, 2026

Observation timestamp: `2026-11-01T19:14:22.826831+05:30` (Asia/Kolkata, UTC+05:30).
Application implementation under test remains `220fa91`; only verification
reporting changes in this evidence branch. This entry supersedes the incomplete
setup snapshot below where explicitly noted.

## Findings addressed and remaining

| Finding | Current evidence |
| --- | --- |
| P0: no valid Engineering request | RESOLVED. A real requester message created task `55d2b9c6-fcc3-5457-adac-ef3535a1aef6`, observed `AWAITING_APPROVAL`, version 1, with zero handoffs. |
| P0: no dedicated managed Codex agent | OPEN. Console control API still returns `agents: []`. Read-only Console database inspection found only `Local Switch server` at `http://localhost:52719`, managed by Console, rather than the required Docker Core server `http://127.0.0.1:8080`. The UI shows a buddy on that separate server; this is not the dedicated acceptance agent. |
| P1: publication targets main flip repository | DESTINATION RESOLVED. Created private disposable repository [parthgenx/switchcrew-acceptance-20261101](https://github.com/parthgenx/switchcrew-acceptance-20261101). All isolated PM/Release config and the new workflow bind that repository. The narrowly scoped Release token is still unconfigured; the broad local CLI login was not copied into Release. |
| P1: acceptance gates missing | PARTIAL PROGRESS. Valid-request gating and separate human Engineering approval have real evidence below. Managed implementation, independent QA, release review/approval, actual draft PR/receipt and full recovery are still NOT RUN. |

## Disposable repository and fixture

The repository is private. It was seeded only with an operator baseline on
`master`: `5b84e5489dd46f136dd9cb818563a8795af08e93`.
The baseline contains a minimal README and deliberately broken `answer.txt`.
It is not an Engineering implementation and is not a QA-passed SHA. The earlier
local fixture copied from flip is retained separately for audit, and is no
longer the configured source. No main-project code was published to the test
repository. GitHub PR enumeration returned an empty list.

Before changing destination, the isolated application DB had zero approval
workflows. Only the isolated PM was stopped and restarted to reload its
in-memory destination. Its fresh connection at `2026-11-01T13:27:22.700317Z`
(`18:57:22.700317 IST`) used generation `8589934801`; its role was assumed at
`13:27:22.774257Z`. The established deployment was not reconfigured or restarted.
The subsequent human request durably binds only the disposable destination.

## Live pre-approval and authorization evidence

Original isolated thread:
[approval-release-success](https://discord.com/channels/1565566745622872115/1566438888992542903).

| Event | UTC / IST | Evidence |
| --- | --- | --- |
| Valid Engineering request by requester `806859717728927744` | 13:33:34.452 / 19:03:34.452 | [request](https://discord.com/channels/1565566745622872115/1566438888992542903/1566445097774743794) |
| Requester attempted to approve | 13:37:07.600 / 19:07:07.600 | [unauthorized attempt](https://discord.com/channels/1565566745622872115/1566438888992542903/1566445991782256651) |
| Separate authorized human `703216309684666389` approved Engineering | 13:39:33.312 / 19:09:33.312 | [human approval](https://discord.com/channels/1565566745622872115/1566438888992542903/1566446602942423232) |

Before the authorized approval, direct reads showed task
`55d2b9c6-fcc3-5457-adac-ef3535a1aef6` in `AWAITING_APPROVAL`, version 1,
workflow phase `awaiting_engineering_approval`, zero handoffs and no recorded
decisions. The requester is absent from the configured approver allowlist.
The unauthorized same-thread attempt left those values unchanged. This is a
real incidental authorization denial on the success task; the separately
requested denial-case tasks are still NOT RUN.

The authorized decision was persisted at
`2026-11-01T13:39:33.857153+00:00`, actor `703216309684666389`,
`approved: true`, `task_version: 1`. Task state became `IN_PROGRESS`, version 4.
Exactly one pending Engineering handoff exists:
`f5230d6d-2c51-577a-8d29-9f6d25f014da`, from PM
`ba0793d3-2fcc-46ab-86dc-96ced12cb8b9` to Engineering
`27e7bb11-8f18-4cae-85c4-118c816d80a7`. No agent-authored approval or manual
state/approval update was used.

## Prepared assignment and remaining execution gate

With Engineering still stopped, the documented operator preparation call used
the actual approved handoff and `session=None` with the normal authorization
check. Assignment ID: `edeb8980baf8440caedffabba4c1d4f5`.
Prepared checkout:

```text
/Users/parthbhat/Coding/New Hackatho /flip/.switch/approval-acceptance-220fa91/engineering/edeb8980baf8440caedffabba4c1d4f5
```

Base SHA is `5b84e5489dd46f136dd9cb818563a8795af08e93`. The ledger contains
one row at phase `reserved`; there is no managed session ID, creation claim,
delivery claim or result. Preparation returned `blocked_configuration` /
`managed_codex_session_not_configured`, as expected for this operator-only
preparation call. The runtime was not started with placeholders, and its
handoff was not completed as blocked by this call.

The isolated PM exited 1 at `2026-11-01T13:39:33.910346Z`, logging
`worker_failed` / `RuntimeError`, when its pending targeted handoff encountered
the still-offline Engineering recipient. The approval and handoff remain
durable. This does not show successful Engineering transport acceptance.
Resume the isolated workers under an on-failure supervisor once the managed
agent/location is configured; do not edit the handoff or restart production.

Connect Console via Add server → **Connect to an existing server** using its
single Core-address field `http://127.0.0.1:8080` (not the separate dashboard
port 3001 and not “Run a server”). Do not alter the other managed local server.
The dedicated Codex agent must use the prepared path above, join the isolated
room and allow the Engineering sender. Its exact Console ID, Switch registration
ID, provider login and location still need verification before delivery.

Before starting Release, supply a repository-scoped credential restricted to
the disposable repository with Contents and Pull requests write permissions.
Store it only in the protected Release config. Actual implementation commit,
tested SHA, QA commands/exits, release approval/version, PR URL/head SHA and
thread completion receipt are absent and remain **NOT RUN**. No release branch,
PR or merge has been performed by this acceptance workflow. No exactly-once
implementation or publication claim is made.

Raw structured evidence is private under
`.switch/approval-acceptance-220fa91/`: `review-remediation-evidence.json`,
`review-discord-decisions.json`, `checkout-preparation.json`, and updated
manifest/configuration files. Secrets are excluded from Git.

---

# Approval and Release live acceptance preparation — November 1, 2026

Latest observation: `2026-11-01T18:44:16.577677+05:30` (Asia/Kolkata, UTC+05:30).
This is a new isolated acceptance setup, not evidence that the complete workflow
has passed. No application code was changed for this run.

## Checkout and controlled validation

The separate `engineering-qa` worktree was clean at
`0b1dce7aa66da51810b2d8d1f5e0fd850ea10bf0` on `codex/engineering-qa`.
`git pull --ff-only origin master` fast-forwarded it to
`220fa91e18e0b3d517d0bee1dc675f49ffa10315`.
`src/switchcrew/approval.py`, `release.py`, `docs/APPROVAL_RELEASE.md` and
`tests/test_approval_release.py` all exist. Read the approval/release guide,
Engineering/QA guide, memory instructions and previous verification records.
The first automatic permission review timed out; the permitted retry succeeded.

`uv sync --frozen` succeeded. Full controlled suite: **189 passed, 3 skipped**.
`ruff check .`, `ruff format --check src tests scripts` and `git diff --check`
passed. Those controlled tests, including Console/GitHub doubles, do not prove
live provider execution, human decisions or publication. Skipped live tests
remain open gates.

## Isolated setup actually performed

The existing deployment checkout, `.env`, databases and volumes were preserved;
no established application service was rebuilt/reconfigured/restarted by a
Compose command. Docker Desktop had been stopped and was opened to restore
host/server availability; daemon-managed containers may start under their own
policies. Switch readiness subsequently returned HTTP 200. Docker Engine is
`29.8.1`; Switch source stays pinned to
`557b62b23002548baa678339a97c8140c1ccaa8b`.

Fresh channel:
[approval-release-acceptance](https://discord.com/channels/1565566745622872115/1566393931837472809).
After confirming the requester actually posted here rather than in `#general`,
a thread was attached to that already-bridged human message:
[approval-release-success](https://discord.com/channels/1565566745622872115/1566438888992542903).

- Switch room: `fdcbbd2b-9490-4fe4-891b-3db34a22ce8e`.
- Discord bridge: `fddc56cd-4e79-4cc7-9110-932bb65f52e0` (existing bridge, fresh channel/room).
- Human anchor Discord message: `1566438888992542903`.
- Requester: Discord `806859717728927744`; allowed to submit, excluded from approvers.
- Designated human approver, supplied by user: Discord `703216309684666389`.
- Destination supplied by user: `Abhigyan-Shekhar/flip`, base `master`.
  This is the main project repository, not a disposable repository. Resolve that
  mismatch explicitly before any release; no release approval has been issued.

Distinct newly registered identities:

| Role | Registration ID |
| --- | --- |
| PM | `ba0793d3-2fcc-46ab-86dc-96ced12cb8b9` |
| Research compatibility identity (not started) | `653c33f5-6617-4377-a1e1-ebb501bfcc2f` |
| Engineering | `27e7bb11-8f18-4cae-85c4-118c816d80a7` |
| QA | `78a59784-c2b6-4b47-b2b1-3d191c1efec1` |
| Release | `ba16286d-9de9-4118-982e-b46366bd66ac` |

Verified Discord bindings use stored transport/client mappings rather than
inferred sender IDs. Room-scoped addressing was configured for these test
identities. Protected native registries and configs live under ignored
`.switch/approval-acceptance-220fa91/`; no key is included in this report.

The new PM process connected at `2026-11-01T13:09:57.183979Z`
(`18:39:57.183979 IST`) with connection
`dbc45d1b-6c78-566c-adab-913bcdaac7e3`, generation `8589934799`; its `pm`
role was assumed at `13:09:57.225003Z`. Engineering, QA and Release were not
started while provider configuration and approvals remain incomplete.

All configs select the identical native application path
`.switch/approval-acceptance-220fa91/application.sqlite3`; Python `samefile`
checks across PM/Engineering/QA/Release paths passed. The observed application
file device/inode is `16777231/21870397`. Engineering/QA/Release all select the
same separate `executions.sqlite3`, device/inode `16777231/21870401`; its path
is verified distinct from the application database. No container `/app/data`
path is used by these native processes. Config path equivalence is verified;
actual file opening by the three unstarted processes remains untested.

An isolated local operator fixture was prepared from the new checkout with
`answer.txt` deliberately containing `broken`. Its setup commit is
`dd0b258a8badc5ce20c3c055a406423606a820f1`. This is an operator setup artifact, **not** a
managed Codex implementation or a QA-tested success SHA. Independent operator
QA templates specify a Python assertion that the file equals `correct`, and a
separate deliberately failing command returning exit 7. Neither template has
run against a live managed implementation.

## Live observations and case status

The requester sent the intended engineering JSON with an extra period after
its closing brace. The parser safely treated this as invalid JSON and created
clarification task `803b1159-f4ae-5c5f-9cb8-7207304c3c50`,
`NEEDS_CLARIFICATION`, version 1. A corrected JSON request was requested;
no state or approval record was manually edited to force it through.

| Case | Result | Actual evidence or missing prerequisite |
| --- | --- | --- |
| Required implementation files / fast-forward update | PASS | All files present at `220fa91`; clean initial worktree. |
| Controlled tests and lint | PASS | 189 passed, 3 live skips; lint/format/diff checks passed. |
| Fresh identities, room and human-anchored thread | PASS | IDs and link above; verified stored sender mappings. |
| Shared application and separate ledger config paths | PASS | `samefile` checks and distinct device/inode evidence above. Unstarted processes still need runtime confirmation. |
| Malformed request cannot launch Engineering | PASS | Clarification task, no Engineering handoff or session. This is not a substitute for valid-request approval acceptance. |
| Valid engineering request stays `AWAITING_APPROVAL` | NOT RUN | Corrected request not yet observed at latest check. |
| Authorized separate human Engineering approval | NOT RUN | No valid awaiting-approval task or human decision yet. |
| Real managed Codex change, commit/diff/session | NOT RUN | Console control API returned HTTP 200 with `agents: []`; Console still points to an unconnected local-server entry. |
| Independent exact-SHA QA → `HUMAN_REVIEW` | NOT RUN | No live managed implementation artifact or tested SHA. |
| Separate release approval after diff/QA review | NOT RUN | No reviewable live diff, QA result or human release decision. |
| Exactly one real draft PR with approved head | NOT RUN | No release approval, configured process-private repository-scoped Release token or publication attempt. |
| Receipt in original Discord thread | NOT RUN | No actual draft PR exists for this test. |
| Unauthorized / wrong-thread / stale or replayed approval denial tasks | NOT RUN | Separate live decision messages/tasks not performed. Controlled tests are separate. |
| Human rejection of Engineering | NOT RUN | No live authorized rejection decision. |
| Failing operator QA blocks publication | NOT RUN | Failing template prepared; no live managed artifact. |
| Human rejection of release | NOT RUN | No live `HUMAN_REVIEW` task. |
| Revision changed after QA cannot publish | NOT RUN | No live tested implementation revision. |
| Restart at pending stages without duplicate work/PR | NOT RUN | No approved live implementation/publication to reconcile. |
| Controlled reversible uncertain-PR-response live fault | NOT RUN | No real PR creation attempted; no blind retry or erased ledger. |

At latest direct database inspection: `{"approval_workflows": 0, "handoffs": 0, "tasks": 1}`;
execution ledger rows: `0`. Recorded tasks: `[{"id": "803b1159-f4ae-5c5f-9cb8-7207304c3c50", "status": "NEEDS_CLARIFICATION", "version": 1}]`.
There are no human approval records, Engineering/QA/Release handoff IDs, real
managed-session IDs, live QA exit codes/tested SHAs, PR URLs or PR head SHAs to
report. No exactly-once success claim is made from those empty counts.

## Prerequisites still needed

Console was opened; its control endpoint again reports zero managed agents.
Its visible default local server says not running even though the separate
Docker Switch API is reachable. Connect Console to gateway
`http://127.0.0.1:3001`, API `http://127.0.0.1:8080`, using the existing locally
stored admin credential; keep the password private. UI automation encountered
capture/window errors, so operator completion was requested. Then configure a
real Codex provider login. Do not start an unrelated Console-managed local stack.

After a valid request and a separate authorized human Engineering approval,
keep Engineering stopped, derive the exact assignment path from the durable
handoff, prepare its checkout, onboard a dedicated Console Codex agent there,
and complete the operator template. Start Engineering only then. Preserve both
human gates; do not replace them with agent-authored approval messages or manual
SQLite changes. Before Release, show the tested SHA, diff, QA commands/exits and
explicit destination to the human, and wait for a separate release approval.

GitHub authentication succeeds through the local keyring when checked with
appropriate keychain access. The earlier sandboxed `gh auth status` failure was
not evidence of invalid credentials. A broad CLI login was not copied into the
Release process; its scoped credential configuration remains incomplete.
No release branch or draft PR was created by this test. No PR was merged.

---

# Operator-provided Discord recovery evidence — November 1, 2026

The user supplied a copied Discord transcript and reported successful restart,
Research-offline recovery, and recovery of a request submitted while PM was
stopped through a forced history gap. This is evidence from the verification
Mac supplied by the operator, not a new live run on the development host.

Source: attached `Pasted text.txt`, SHA-256
`1e631af4435f7dad96418e928f2190f27de4e43e2042af09cf7ce84e045a5f84`.
Displayed times below are copied from Discord; the export does not specify its
timezone. The user identifies the successful location as the existing
“SwitchCrew anchored verification” thread linked in the historical record below.

| Visible exchange | Evidence in supplied transcript |
| --- | --- |
| Baseline on reported `master` revision `50e24b2` | Human marker `master-50e24b2-normal` at 1:41 PM; task `45b1fc57-a15d-55c3-8392-a217feaab298`, handoff `4653f91b-95bf-52db-a52d-26323fca84d8`; Research acknowledgment/evidence and PM result by 1:42 PM. |
| Request during operator-reported forced gap | Human marker `master-50e24b2-forced-gap` at 1:43 PM; PM delegation, Research acknowledgment/evidence and PM result at 1:46 PM. Task `04a70def-5e99-5b2d-9c4f-19d0b155e447`; handoff `d49c5df2-fafe-50ea-96c3-df2d9c1feaeb`. |
| Logical delivery identities for that recovery | Delegation `bb3e4ed3-821e-4f9d-a8a4-ddd085ab7a02`; acknowledgment `b71dfa7a-58e2-48eb-a13a-ce1af417a0eb`; Research completion `e1c402c2-2c48-43de-9270-fb41aaf21a0c`. These are application delivery IDs, not Discord message IDs. |

The supplied excerpt shows one visible delegation/acknowledgment/completion
sequence for the forced-gap task, with consistent task/handoff IDs. The operator
reports exactly one execution and a result in the same Discord thread. The
copied text alone does not establish the stop/restart procedure, actual server
`gap` emission, thread ID, or database execution counts. Correlate the above IDs
with the verification Mac's logs/database to independently audit those claims.
The earlier “After gap” row below concerns a different run and retains its
original, narrower conclusion.

The transcript contains PM/Research only. It does not demonstrate Engineering,
QA, human approval gates or GitHub publication. Research reports
`evidence_found`, explicitly leaves criteria assessment to a human, and reports
memory as `disabled_or_no_write`; these results are not independent QA approval
or evidence of product Waggle persistence.

Approved PM → Engineering routing, independent QA orchestration and approved
Release/GitHub draft PR creation are now implemented in this repository,
with controlled local validation recorded in
[the current status](../../docs/DAY1_STATUS.md). They were not part of the
reported `50e24b2` demonstration and remain live-unverified.
Follow [the approval/release runbook](../../docs/APPROVAL_RELEASE.md) to provision
the real Console-managed Codex agent and test both authorization stages, failed
QA blocking publication, and the final actual draft PR receipt.

---

# Recovery hardening follow-up — November 1, 2026

Workstream `codex/recovery-acceptance`, based on live-verification commit
`2441365`. The current host has no working Docker installation, live Switch
listeners or external worker credentials. The user confirmed that the demo
below ran on a friend's Mac, with private files in that Mac's `.env` and
`.switch/`. No new real Discord, container or server recovery run is claimed.

## Changes and local evidence

- Fenced reconnects send `expected_generation`; a client that missed its
  eviction cannot silently reclaim a newer stream. Heartbeat and operation
  `taken_over` refusals use a distinct terminal ownership-loss exception.
- `heartbeat_lapsed` eviction frames reconnect with the existing cursor rather
  than being treated as terminal ownership loss. HTTP 500 during stream/beat
  reads also reconnects; outgoing mutations are not automatically retried.
- PM/Research log safe connection, role and replay metadata. Lost ownership
  exits 0; ordinary failures exit 1. New `worker-recovery.yaml` uses `on-failure`
  so a displaced holder stays stopped. This override is required; the base
  Compose policy still restarts clean exits. It trades automatic Docker-daemon
  restart startup for explicit operator startup of workers.
- Controlled regression: a request found only in gap history, duplicated across
  pages and live events, survives database reopen and produces one task/handoff,
  one Research execution and one logical result in its stored original thread.
  Interrupted history insertion leaves the old durable cursor for retry.
- `gap_cursor.py` backs up and injects only the application cursor, restores
  only an unchanged injected cursor and refuses to overwrite backups/progress.
  Subprocess tests exercise those refusal and restore paths on temporary SQLite.
- Final local checks: **136 tests passed; 3 opt-in live tests skipped**. Ruff
  lint and format, shell syntax and `git diff --check` passed. Both worker
  restart override entries parse as YAML; actual Docker Compose execution was
  unavailable here.

The three live probes separately cover real heartbeat expiry, `no_stream`
reattachment and `taken_over` fencing using an isolated probe identity. They
are present and runnable, but were **not executed against Switch** on this host.
Read the [acceptance runbook](RECOVERY_ACCEPTANCE.md) to run them and the
request-during-gap procedure on the verification Mac. Also verify the actual
supervisor's terminal takeover behavior there. Natural retention expiry,
fresh provisioning and the desktop Console connection remain separate gates.

---

# Live deployment verification — November 1, 2026

Executed in the macOS checkout used for this run. The earlier records below
refer to other hosts or earlier states and remain historical.

## Provisioned and running

Switch remains pinned to `557b62b23002548baa678339a97c8140c1ccaa8b`.
Docker Desktop was started; existing generated server configuration and the
PostgreSQL volume were preserved. Switch readiness, gateway and SwitchCrew API
return HTTP 200. PM and Research authenticate separately and run as containers.
Console v0.40.1 is installed and was opened; its desktop connection to this
Docker server remains unverified. Provisioning used the authenticated gateway
API, including the built-in Discord text bridge, rather than Console's forms.

The existing bot token was validated without printing it. The bot belongs to
the requested guild and can access the test channel. The original owner was
linked; the operator explicitly authorized adding `lonewolf6786` as a second
owner during this run. Both account links were verified through Discord
membership and Switch's stored external-user/client mappings. Credentials and
identity evidence remain in ignored mode-0600 files under `.switch/` and `.env`.

| Item | Verified identity |
| --- | --- |
| Switch room | `5689b0df-8b85-4e5d-890f-7e8ee9dced1a` |
| Discord bridge | `fddc56cd-4e79-4cc7-9110-932bb65f52e0` |
| PM registration | `23b2ffba-67c1-4300-83e5-4ac4f2fd42da` (`pm-agent`, display “PM Agent”) |
| Research registration | `36e9e040-87b6-4e62-9a80-a13edd0831da` (`research-agent`, display “Research Agent”) |
| Original owner | Discord `703216309684666389` |
| Additional authorized owner | Discord `806859717728927744` |
| Guild / channel | `1565566745622872115` / `1565566746423992432` |
| Successful Discord thread | `1566349550032134197`, “SwitchCrew anchored verification” |

Membership UUIDs and SSE transport sender IDs differ. Sender settings were
verified against the stored client mapping and authenticated history, never
inferred from a display name. Both workers have room-scoped addressing rules
allowing the two verified humans and the peer worker. Roles `pm` and `research`
are defined and assumed on each worker connection.

## Live workflow evidence

[Original test thread](https://discord.com/channels/1565566745622872115/1566349550032134197).
Research uses the official bounded source
`https://docs.discord.com/developers/events/gateway.md`.

| Gate | Evidence / result |
| --- | --- |
| Full workflow | Task `e02cc046-07b1-5323-a652-3964cafbf34c` completed in the original thread. Human request `1566350977920016495`; PM delegation `1566350987806117890`; Research acknowledgment `1566350993669496925`; Research evidence `1566351000007086201`; PM result `1566351004113444906`. |
| Targeted delivery | Outbox records became sent only after the recipient status was `live`; Research's persisted `handoff.accepted` followed transport acceptance. |
| Restart | Restarted API, PM and Research. Task/handoff/inbox/outbox counts stayed `2/2/8/18`; completed task and handoff results persisted without another research execution. |
| Research disconnect | Stopped Research, submitted request `1566351241825747029`; task `f92baf72-3b4c-5cab-984a-2a56d8967046` was `IN_PROGRESS`. Restarting Research produced acknowledgment `1566351275518595206`, evidence and PM result `1566351289128980625`; task became `COMPLETED`. |
| SSE interruption | Restarted the Switch server. Workers resumed from durable cursors with stable connection identities. PM reconnected in process; Research logged an HTTPStatusError, exited and recovered through Docker restart policy. Completed research was not repeated. This exercises interrupted SSE/reconnection and process recovery, not every heartbeat refusal case. |
| Server-emitted history gap | Stopped PM and temporarily moved its existing replay-ticket row `9007194959773739` to a reversible alternate ID. The real server emitted `gap`; PM logged `history_gap_received` then `history_gap_replayed` at `07:24:25 UTC`. The original ticket was restored in `finally`. No task history or events were deleted. This fault models an unavailable retained replay ticket, not natural retention expiry. |
| PM disconnect | With PM stopped, human request `1566353060283023391` was retained and completed after restart as task `267f53e0-2594-5329-a704-2aa658a83e66`. This additional run exercised normal durable reconnection, not a second gap. |
| After gap | Prior completed tasks remained completed without repeat handoffs. An older unprocessed plain message correctly created one `NEEDS_CLARIFICATION` task. A new request after replay created task `792d9c31-4417-59fd-ab30-210440cb6c63`, which completed with source evidence. This run does not establish recovery of a request submitted during the gap itself. |

Connection IDs are PM `82ba9c24-416a-5565-9dbc-79158e14bfb5` and Research
`73ebf199-e33b-5870-8049-6fee059b991f`. Durable cursor snapshots, task records,
Discord message evidence and the replay-ticket backup are stored privately
under `.switch/`. Logging now exposes only allowlisted connection/replay fields.

## Findings and checks

The supplied registration labels containing spaces were rejected by the actual
pinned server. Registration now accepts separate machine/display names and
validates the machine name before reserving a credential path. Worker defaults
and deployment instructions use the machine names for targeting.

The official Gateway HTML page exceeded the 256 KiB source limit. Its official
Markdown representation is approximately 55 KB; Markdown content is now
accepted while retaining the size limit and public-network checks. The first
test remains honestly recorded as `BLOCKED` due to that original source error.
A standalone bot-created thread had no ingested root and replies reached the
parent channel. The successful test used a thread on an already-bridged human
message; this upstream thread-root limitation remains documented.

`python3 deploy/switch/preflight.py` passed runtime, private configuration,
health and separate worker-key authentication checks. Its operator gates remain
`not_verified` by design: their evidence belongs in this report, not inferred
from health. Final automated checks: **122 pytest tests**, `ruff check .`,
`ruff format --check src tests scripts deploy/switch/preflight.py`, and
`git diff --check`.

---

# Local deployment verification — October 29, 2026

## Current checkout recheck — October 29, 2026

After fetching commit `729699a`, this checkout's current host did **not** have
the deployment described below: Docker CLI resolves only to a broken symlink
targeting an absent Docker.app, Switch Console is absent, and `.env` and
`.switch/` are absent. HTTP probes to Switch `8080`, gateway `3001` and SwitchCrew
`8000` could not connect. Podman has a stopped `company-brain` VM; it was left
unchanged. A root `.env` was subsequently created with mode 0600 and a random
SwitchCrew API token; external credentials, rooms, bindings and allowlists remain
empty. No container runtime or external credentials were installed.

Run `python3 deploy/switch/preflight.py` to inspect the current host without
printing credentials. Treat the following earlier record as historical evidence,
not proof that services are available in a different checkout or execution host.
Discord workflow and live recovery acceptance remain unverified here.

## Earlier recorded deployment

Executed on an Apple Silicon macOS host with Docker Engine 29.8.1 and
Compose v5.5.1. This is a local deployment; no remote hosting was provisioned.

## Running services

| Component | Address / version | Verified result |
| --- | --- | --- |
| Switch | `http://127.0.0.1:8080`, source `557b62b23002548baa678339a97c8140c1ccaa8b` | `/health` and `/health/ready` HTTP 200; database, listeners, signal bus and bridges ready |
| PostgreSQL | `127.0.0.1:5433`, `postgres:16-alpine` | Container healthy; init-db completed with exit 0 |
| Operator gateway | `http://127.0.0.1:3001` | HTML HTTP 200; admin login and authenticated session HTTP 200 |
| SwitchCrew API | `http://127.0.0.1:8000` | Built from repository Dockerfile; container healthy; `/health` HTTP 200 |
| Switch Console | `/Applications/Switch Console.app`, v0.40.1, arm64 | Official ZIP SHA-256 matched; macOS accepted notarized Developer ID; application launched and reported running |

The server's login response reports Switch Core 0.31.0 and `gateway-api`
contract 1, which Console v0.40.1 also supports. Desktop UI automation timed
out repeatedly while acquiring the Console window; a connection from Console
to this Docker server has **not** been verified. To finish that check, choose
**Connect to an existing server** and use the addresses and credential location
in [the deployment guide](README.md).

## Commands exercised

```sh
bash -n deploy/switch/local.sh
bash deploy/switch/local.sh up
# Repeated successfully with the same source, env and PostgreSQL volume.
bash deploy/switch/local.sh up
docker compose up --build -d --wait api
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:8000/health
```

Gateway login and session checks used a Python HTTP cookie client reading the
generated local env file directly; no credentials were printed. Final Docker
status showed Switch, PostgreSQL and SwitchCrew API healthy, with the gateway
running. Readiness was rechecked after changing the Switch health check from
liveness to readiness. All exposed deployment ports bind to `127.0.0.1`.

Existing application checks passed: **121 pytest tests**, `ruff check .`, and
`ruff format --check src tests scripts`. No application behavior was changed.

## Remaining live integration

Desktop connection confirmation, Discord bot/owner linking, agent registration,
PM/Research worker configuration, room addressing and end-to-end handoff/recovery
acceptance remain pending. Infrastructure health does not establish those gates.
Generated secrets, downloaded app files and upstream source live outside tracked
files. Keep `.env` and `.switch/` private and preserve the Docker data volume.


---

# Master deployment acceptance — November 1, 2026

This run used the verification Mac and the clean deployment checkout at
`/Users/parthbhat/Coding/New Hackatho /flip`. Execution window was approximately
13:39–13:48 IST (08:09–08:18 UTC). Timestamps below are UTC, with IST obtained by
adding 05:30. Private `.env`, `.switch/` and existing Docker volumes were
preserved. No application source was changed for this acceptance run.

## Versions and deployment

- Application/master commit: `50e24b272be99567b5411b435ebf744c6519ebb4`.
- Switch source: `557b62b23002548baa678339a97c8140c1ccaa8b` (unchanged pin).
- Runtime versions: Docker Engine `29.8.1`, Compose `5.5.1`, Console `0.40.1`.
- Console control GET `/agents`: HTTP 200, empty agent list. No managed Codex
  session ID or live Engineering tested SHA exists for this run.
- Application image: `sha256:5ec9b4f148b66df578ad9ff88f5993f8cddcc9262908d32dd4f56bf679fcca87`.
- PM image: `sha256:43fb4b0319310db6c9ffa4a3acfd99f0e0273aaa1fe053dcf7682e593b16f30d`.
- Research image: `sha256:e685222b3c97764e956d28e68a4d6cbd4e048bd95c2b083c88291e80f213d131`.

Executed `git fetch origin`, `git switch master`, `git pull --ff-only origin
master`, `uv sync --frozen`, then `uv run pytest -q`: **171 passed, 3 skipped**.
Those skips remained open until the separately opted-in live run below, which
had **2 passed, 1 failed**. This report does not treat the initial skips as passes.
`ruff check .`, `ruff format --check src tests scripts` and `git diff --check`
passed.

Rebuilt API, PM and Research using all three Compose files, including
`deploy/switch/worker-recovery.yaml`. Docker inspection confirms both workers
use `on-failure`; API retains `unless-stopped`. Initial post-deploy connections
and role assumptions were observed at `08:10:14.959` (Research) and
`08:10:15.065` (PM), generations `8589934691` and `8589934692`.
Preflight passed Docker/Compose, private configuration, health and separate
worker authentication. Both workers and the healthy API remain running after
the final restart.

## Normal Discord workflow — PASS

Original anchored thread:
[SwitchCrew anchored verification](https://discord.com/channels/1565566745622872115/1566349550032134197).
It is attached to an already-bridged human message. This run reused the verified
owner bindings and the official configured Discord Gateway Markdown source.

- Task: `45b1fc57-a15d-55c3-8392-a217feaab298`, final `COMPLETED`.
- Research handoff: `4653f91b-95bf-52db-a52d-26323fca84d8`, `completed`, version 2.
- Switch source: `sw_a6c0f9184b3d7f6551bb27153f872501`; original Switch thread:
  `sw_bcc6bd25ce4e6aff1355d594b96573da`.

| Event | UTC | Discord evidence |
| --- | --- | --- |
| Human request | 08:11:57.868 | [request](https://discord.com/channels/1565566745622872115/1566349550032134197/1566364162035159071) |
| PM reports delegation | 08:11:59.898 | [delegation](https://discord.com/channels/1565566745622872115/1566349550032134197/1566364170549596191) |
| Research explicitly acknowledges | 08:12:01.210 | [acknowledgment](https://discord.com/channels/1565566745622872115/1566349550032134197/1566364176052523090) |
| Research returns evidence | 08:12:02.690 | [evidence](https://discord.com/channels/1565566745622872115/1566349550032134197/1566364182260092949) |
| PM reports result | 08:12:05.575 | [result](https://discord.com/channels/1565566745622872115/1566349550032134197/1566364194360659968) |

Restarted PM and Research with the required override. Task/handoff/inbox/outbox
counts stayed **8/6/26/59**, and the completed task/handoff result persisted.
No duplicate completed Research execution was observed.

## Live heartbeat and supervisor checks

Disposable probe registration: `04494ed7-386a-4d7c-849d-4ff5936b46f9`, name
`recovery-probe-acceptance`. No production PM/Research credentials or connection
IDs were used by these probes. The live test run began at
`2026-11-01T08:10:39.055947+00:00` and lasted about 40.8 seconds.

| Gate | Result | Actual observation |
| --- | --- | --- |
| Heartbeat expiry reconnect | PASS | Real server TTL expiry reopened the stream under the same random connection ID with a newer generation. |
| `no_stream` reattachment | PASS | Closing the stream yielded HTTP 409 `no_stream`; fenced reattachment advanced generation and accepted a heartbeat. |
| `taken_over` strict losing-adapter behavior | FAIL | Old-generation beat returned HTTP 409 `taken_over` and the loser ultimately raised `SwitchOwnershipLost`, but the losing adapter made **two** stream-open requests; the test required one. Connection: `e1f3d33f-833e-43c9-b486-e22eeee2b6ab`. The second open was fenced; the no-reopen assertion remains unresolved. |
| Supervised worker loser stays stopped | PASS | Separate actual worker container exited 0 after ownership loss, restart count remained 0, and the winning client continued accepted heartbeats for 12 seconds. |

The supervised test used an isolated internal room
`dcf82b4c-05af-412f-9dfe-e06405379b2b`, a separate application database/volume
`flip-recovery-probe-data`, and container `flip-recovery-supervised` with
`--restart on-failure`. It did no task work. Its stable test connection was
`81fde3c1-43d2-5f02-a96d-e796e8b38880`.

The losing worker connected at `08:11:47.812422`, generation `8589934700`, and
assumed its role. At `08:12:04.570946` it logged `worker_ownership_lost` and
exited 0. The winner held generation `8589934701` and accepted heartbeats through
`08:12:16.859580`. Later inspection still showed `exited`, exit 0, restart count
0. The isolated container is retained stopped, and its volume is preserved.
The disposable registration was revoked with HTTP 200 after testing. Pytest's
long failure traceback included its disposable key; revocation invalidated that
key. No traceback or credential is committed in this report. Detailed test
artifacts stay private under `.switch/`.

The failing strict adapter probe must be resolved before claiming all heartbeat
acceptance passed. Its source and test were not changed to weaken the assertion.
The supervised pass does not erase this independent failed gate.

## Request submitted during forced history gap — PASS

Used the runbook's **application-cursor backup procedure**, not the earlier
Switch-ticket-row procedure. Both workers were stopped at approximately
`08:12:34`; API and Switch remained running. `gap_cursor.py arm` fsynced private
backup `/app/data/gap-master-20261101T081234Z.json` in the existing data volume,
then changed only PM's application cursor from `9007194959773786` to
`9223372036854775807`. No Switch tickets, history or task records were deleted
or renamed.

The human posted exactly one marked request at `08:13:41.063`, while PM was
stopped. Authenticated Switch history independently confirmed its presence
before PM started. Research was started first and logged a fresh connection and
role assumption at `08:15:50.340945` / `08:15:50.391333`.

PM startup and replay evidence:

| UTC | Observation |
| --- | --- |
| 08:16:06.475650 | `worker_connected`, generation `8589934705`, cursor `9223372036854775807` |
| 08:16:06.529993 | `worker_role_assumed`, PM |
| 08:16:06.543254 | `history_gap_received` |
| 08:16:06.544917 | `history_gap_replayed`, durable inbox ingestion, resumed cursor `9007194959773790` |

Task: `04a70def-5e99-5b2d-9c4f-19d0b155e447`, final `COMPLETED`.
Research handoff: `d49c5df2-fafe-50ea-96c3-df2d9c1feaeb`, `completed`, version 2.
Source message: `sw_64c7bf3f928b20708793d0ff86cde4f9`, same original Switch thread
`sw_bcc6bd25ce4e6aff1355d594b96573da` and Discord thread as the normal test.

| Event | UTC | Discord evidence |
| --- | --- | --- |
| Request while PM stopped | 08:13:41.063 | [request](https://discord.com/channels/1565566745622872115/1566349550032134197/1566364594866229340) |
| PM delegation | 08:16:08.675 | [delegation](https://discord.com/channels/1565566745622872115/1566349550032134197/1566365213995835523) |
| Research acknowledgment | 08:16:09.349 | [acknowledgment](https://discord.com/channels/1565566745622872115/1566349550032134197/1566365216823058466) |
| Research evidence | 08:16:10.846 | [evidence](https://discord.com/channels/1565566745622872115/1566349550032134197/1566365223101800551) |
| PM result | 08:16:11.560 | [result](https://discord.com/channels/1565566745622872115/1566349550032134197/1566365226096402462) |

Exactly one new logical task, one Research handoff and one `handoff.completed`
event were recorded. All six application deliveries (handoff, delegation,
acceptance, acceptance report, completion and final report) were `sent`; no task
delivery remained pending. Counts advanced only once from **8/6/26/59** to
**9/7/30/68** (tasks/handoffs/inbox/outbox).

After another PM/Research restart, tasks, handoffs and assignments compared
identical to the pre-restart snapshot, including prior completed research;
counts remained **9/7/30/68**. Cursors advanced normally to PM
`9007194959773801` and Research `9007194959773802`. No repeated completed
Research or additional result delivery was observed. The cursor backup is
retained; it was **not restored after successful replay**, as the runbook
requires. This proves an injected unavailable-ticket gap with an offline
request, not natural retention expiry.

## Real Engineering → independent QA — BLOCKED / OPEN

A fresh Console control probe during this run returned HTTP 200 and
`agents: []`. There is no registered Console-managed Codex provider to start;
no actual managed implementation session, commit/diff or exact live QA-tested
SHA can be supplied. The available Codex CLI alone does not establish the
required Switch-managed session integration. No fabricated session, local CLI
substitute or controlled fixture is presented as live evidence.

| Scenario | Live status |
| --- | --- |
| Real Console Codex small implementation | BLOCKED: dedicated managed Codex agent/session absent |
| Deliberately broken implementation → `qa_failed` | OPEN: no real managed implementation artifact |
| Corrected assignment → exact-SHA `verified_success` | OPEN: no real managed implementation artifact |
| Implementation changes after QA → `stale_qa` | OPEN for live execution; controlled tests only |
| Console unavailable after delivery → pending/reconciled without duplicate | OPEN for live execution; no delivery occurred |
| Cancellation during execution → blocked late result, no QA assignment | OPEN for live execution; no execution occurred |

Provision a dedicated room, coordinator/Engineering/QA/Codex identities,
provider login and isolated target checkout/application DB according to
`docs/ENGINEERING_QA.md`, then run these six scenarios. No existing production
identity or database was repurposed to pretend these gates passed. Automatic
Discord PM routing remains pending as requested. Desktop Console connection,
natural retention expiry and fresh-stack reproduction also remain separate
open gates.

## Evidence retention

Private raw evidence is under `.switch/`: `live-heartbeats.xml`,
`supervised-evidence.json`, `master-gap-run.json`, `master-task-evidence.json`,
`master-message-evidence.json`, `master-offline-room-history.json`,
`recovery-master-*.json` and `master-final-preflight.json`. Keys and raw traces
are excluded from Git. Only this report is changed by the acceptance run.

---

