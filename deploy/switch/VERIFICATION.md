# Master deployment acceptance — November 1, 2026

This run used the verification Mac and the clean deployment checkout at
`/Users/parthbhat/Coding/New Hackatho /flip`. Execution window was approximately
13:39–13:48 IST (08:09–08:18 UTC). Timestamps below are UTC, with IST obtained by
adding 05:30. Private `.env`, `.switch/` and existing Docker volumes were
preserved. No application source was changed for this acceptance run.

## Versions and deployment

- Application/master commit: `50e24b272be99567b5411b435ebf744c6519ebb4`.
- Switch source: `557b62b23002548baa678339a97c8140c1ccaa8b` (unchanged pin).
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
