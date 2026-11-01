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
