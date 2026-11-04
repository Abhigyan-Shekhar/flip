# Local Mac runtime and handoff

Verified November 4, 2026 IST. See the [acceptance checkpoint](VERIFICATION_2026-11-04.md)
for results, fixes and remaining boundaries. These instructions describe the
provisioned Mac, not credentials or running services supplied by a Git clone.

## Deployment layout

| Component | Location / endpoint | State at checkpoint |
| --- | --- | --- |
| Docker runtime | Colima profile `flip`, context `colima-flip` | 4 CPUs, 4 GiB RAM, 20 GiB virtual disk; running |
| Switch | `http://127.0.0.1:8080` | Healthy, source pinned to `557b62b23002548baa678339a97c8140c1ccaa8b` |
| Gateway | `http://127.0.0.1:3001` | Running |
| PostgreSQL | Loopback port 5433 | Running in the Switch stack |
| App API/dashboard | `http://127.0.0.1:8000/dashboard` | Running as a host process |
| PM and Research | Independent host Python processes | Connected to the verified Discord-associated Switch room |
| Codex harness | Locally authenticated CLI | PM planning / Research drafts; not managed Engineering |
| Memory | Private runtime SQLite-backed Waggle process | Isolated `/acceptance/` project scope |
| Switch Console | Installed v0.40.1 | Control API reachable, zero managed agents at last probe |

The runtime used Homebrew `colima`, `docker`, `docker-compose` and `docker-buildx`.
Switch's official source is in ignored `.switch/server`; its PostgreSQL state is
in Docker volumes. The host API/workers share private local SQLite state under
`.switch/runtime`. Do not point host workers at a container-internal `/app/data`
path or start a second API on port 8000 alongside the existing process.

## Access without exposing credentials

- Dashboard: connect using `SWITCHCREW_API_TOKEN` from the private root `.env`.
- Gateway: local administrator email/password are in `.switch/server/.env`.
- Human decisions: use the separately generated `local-operator` credential in
  `SWITCHCREW_APPROVAL_CREDENTIALS`; the service API token cannot approve work.
- Bot credentials: private `.switch/discord-test.env`; registration keys are in
  their respective ignored `.switch/` files.
- Private backups retain the previous app settings under `.switch/runtime/`.

No credential values belong in this document, Git, screenshots or test logs.
Configured human approval capability does not mean any task was approved.

## Start, inspect and stop this existing setup

From the repository root:

```sh
colima start --profile flip
DOCKER_CONTEXT=colima-flip bash deploy/switch/local.sh up
python3 .switch/runtime/manage.py start

python3 .switch/runtime/manage.py status
DOCKER_CONTEXT=colima-flip bash deploy/switch/local.sh ps
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8080/health/ready
```

`manage.py` is a generated, private, **Mac-local helper**, not a tracked program
or part of a fresh clone. It starts API/Research/PM as detached processes, keeps
PID files, and writes private per-process logs under `.switch/runtime/logs`.
After changing settings or installing updated source, use:

```sh
python3 .switch/runtime/manage.py restart
```

Stop without deleting task or database state:

```sh
python3 .switch/runtime/manage.py stop
DOCKER_CONTEXT=colima-flip bash deploy/switch/local.sh stop
# Optional: stop the dedicated VM as well.
colima stop --profile flip
```

Host processes survive the chat, but do not automatically start at macOS login.
A launchd attempt could not access this Documents checkout and was removed;
there is no functioning LaunchAgent to rely on. Switch container restart
policies take effect after its Docker VM is running.

For a **fresh clone**, use [Switch provisioning](../deploy/switch/README.md),
[application operations](OPERATIONS.md) and [organization setup](ORGANIZATION_SETUP.md).
Provision credentials/registrations/room roles first. The tracked host entry points
are `python -m switchcrew.workers pm`, `python -m switchcrew.workers research`
and `uvicorn switchcrew.api:create_app --factory --host 127.0.0.1` in the prepared
environment. Do not run duplicate identities against an existing live connection.

## macOS package-install finding

Tests import `src` directly and therefore did not reveal this Mac's failed
editable-package import. The documented non-editable install fixed it:

```sh
uv sync --frozen --no-editable --reinstall-package switchcrew
uv run --no-sync python -c 'import switchcrew'
```

Retain the installation with `uv run --no-sync`, or include `--no-editable` when
syncing again. Installed imports and the packaged dashboard template were checked.

## Colima execution configuration

The isolated runner's cleanup environment intentionally does not inherit the
user's PATH, HOME or current Docker context. Configure an absolute binary and
explicit socket, replacing the socket placeholder with the real absolute path:

```json
{
  "binary": "/opt/homebrew/bin/docker",
  "daemon_socket": "/Users/YOUR_ACCOUNT/.colima/flip/docker.sock",
  "image": "python@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30"
}
```

The pinned image above was used for the simple acceptance regression; it is not
an installed Security scanner image. Select reviewed images with the actual
operator-configured binaries for real jobs. Put disposable checkouts under the
VM-shared home directory; this VM did not share macOS's `/private/var/folders`
temporary directory. [Isolation policy](ENGINEERING_ISOLATION.md) still applies.

## Discord lifecycle and temporary slash test

The active text bridge uses a temporary bot and verified guild/user/room bindings.
PM and Research keep their own identities. Human identity was linked through the
platform directory and authoritative Switch client records. Signed `/crew`
requests additionally need [verified thread mappings](OPERATOR_DASHBOARD.md#verified-thread-identifiers).

The successful custom HTTP slash test temporarily took over all interactions for
that Discord application. Its endpoint was restored, temporary `/crew` command
removed, and proxy/tunnel stopped afterward, preserving Switch's built-in
commands. `/crew` is therefore **not left continuously deployed** on this bot.
Use a separate app or an all-command dispatcher before permanent hosting.

When retiring the test bot, disconnect its Switch bridge and stop test traffic
before deleting the Discord application. Keep sanitized verification receipts.
A replacement bot needs fresh credentials, app/public-key settings, bridge/owner
bindings and thread mappings; rerun the Discord checks. Do not assume old bridge
identifiers or permissions authorize a replacement.

## Next operator work

Onboard a dedicated Console-managed Codex agent and disposable coding target;
provision independent privileged workers, scanner policy and scoped GitHub Release
credentials; obtain actual human approvals. Separately configure consented real
audio/provider jobs and exact-revision Company Brain indexing. None of these gates
is satisfied merely by leaving this read-only local runtime running.
