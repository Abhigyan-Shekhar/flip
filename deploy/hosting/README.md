# Host Flip on Ubuntu

This directory prepares persistent **core** hosting: pinned Switch/PostgreSQL/
gateway, API, PM and Research. It does not create a cloud server, purchase hosting,
register Discord identities, enroll Codex, or claim a functioning coding-to-PR
pipeline. Engineering/QA/Reviewer/Release remain a separate operator deployment.

## 1. Get a server

If you have no server yet, ask the hosting account owner to create an Ubuntu
24.04 **x86_64** VM with a dedicated nonroot operator account and SSH key access.
Start with 4 vCPU, 8 GB RAM and 80 GB SSD; this is an estimate, not a measured
minimum. Confirm the recurring price in that provider's checkout. This project
does not include cloud billing credentials or an automatic purchasing flow.
ARM support across the complete pinned stack remains unverified.

Record the public IP and operator username. From your own computer:

```bash
ssh operator@SERVER_IP
```

Replace `operator` and `SERVER_IP` with the actual account and IP throughout.
Allow SSH through the host/cloud firewall. Keep application ports private; this
setup uses an SSH tunnel and needs no domain or public dashboard port.

## 2. Prepare the server

Install Docker Engine, Buildx and Compose using the
[official Ubuntu apt repository instructions](https://docs.docker.com/engine/install/ubuntu/).
Configure Docker access only for the trusted operator; Docker access is
privileged. Install Git, curl, Python and
[uv](https://docs.astral.sh/uv/getting-started/installation/).
Run `docker info` and `docker compose version` successfully before proceeding.
Enable Docker at boot with `sudo systemctl enable --now docker`.

Use this hosting change on top of application baseline
`06e6d780ab8bead1b16d8926ccf10528d5bd2fc8`. Deploy the reviewed commit containing
this directory; **do not clone master and assume it contains this change**.
The local implementation branch is `codex/hosting`; it must be pushed or
transferred to the server before that branch can be cloned there.

From that server checkout as the operator:

```bash
bash deploy/hosting/prepare.sh
```

Preparation installs frozen Python dependencies, runs tests/lint/format/build,
creates `.env` at mode 0600 with a new token (never printed), and renders four
systemd units into ignored `.switch/systemd`. Existing private `.env` contents
are preserved. An existing public or symlink `.env` is rejected. No services are
installed or started by preparation. Keep this checkout at the same absolute
path after rendering; rerender after moving it.

## 3. Configure real identities

Start infrastructure for the initial Console setup:

```bash
bash deploy/switch/local.sh up
```

Follow [Switch/Discord provisioning](../switch/README.md) to connect compatible
Console v0.40.1, link the owner, create a test-channel room, independently
register PM/Research, grant room membership and configure peer addressing.
Privately fill `.env` with the real room ID, agent IDs, participant IDs, separate
worker keys, verified Discord bindings, nonempty guild/user allowlists, exact
names `pm-agent`/`research-agent`, and approved public HTTPS Research URLs.
Keep SQLite paths at `data/switchcrew.sqlite3` and `data/checkpoints.sqlite3`.
All native services use this same checkout and database.

Keep `SWITCHCREW_APPROVAL_CREDENTIALS={}` in shared `.env`. Any future API approval
tokens must be supplied only to the API process via its own private environment
file/drop-in. No Release/GitHub credential belongs in shared `.env`, coding
workers, or these generated units. Optional services remain disabled by default.

```bash
.venv/bin/python deploy/hosting/hosting.py check-core
```

This gate checks core configuration only and prints field names, not values.
It blocks missing credentials/identity/bindings/sources, loose `.env` permissions,
and approval credentials in shared `.env`. It does not prove identity ownership,
reachable services, Discord acceptance, or full publication readiness.

## 4. Install and start persistent core services

Review `.switch/systemd/*.service` before installing. These use the current
operator, absolute checkout path, private file creation, and the same native
SQLite layout. Switch retains its upstream pinned revision and Compose restart
policy. Core processes restart on failure; Switch starts after Docker at boot.
The Switch unit is a oneshot launcher, so `active (exited)` is expected: inspect
actual containers and health separately. Application units run the configuration
gate before every start. Stop any manually launched native API/PM/Research first
so they do not compete with systemd consumers.

On the Ubuntu server:

```bash
sudo install -m 0644 .switch/systemd/flip-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/flip-{switch,api,pm,research}.service
sudo systemctl enable --now flip-switch flip-api flip-pm flip-research
systemctl status flip-switch flip-api flip-pm flip-research
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:8000/health
.venv/bin/python deploy/switch/preflight.py
```

Do not run the application Compose services alongside these native workers.
The generated services do not supervise managed controllers or privileged workers.

From your own computer, leave this tunnel open:

```bash
ssh -N -L 3001:127.0.0.1:3001 -L 8080:127.0.0.1:8080 -L 8000:127.0.0.1:8000 operator@SERVER_IP
```

Open `http://127.0.0.1:8000/dashboard` and authenticate with the private API token.
Console uses local gateway 3001 and API 8080 through the same tunnel.

## 5. Prove hosting works

Send a real allowed Discord research request and require the PM → Research
handoff and result in the original thread. Restart a worker and verify durable
replay without duplicate work. Reboot the server and repeat health/roundtrip
checks. Service status alone is not acceptance.

For actual coding, follow [Engineering/QA](../../docs/ENGINEERING_QA.md),
[approvals and Release](../../docs/APPROVAL_RELEASE.md), and the entire
[managed Console contract](../console-controller/README.md). The Linux relay
needs a verified Docker-network route to Switch; `host.docker.internal` does
not by itself reach a loopback-only host listener. The controller bundles,
provider authentication, assignment enrollment and independent checks remain
manual gates. Never fabricate descriptors or give a coding worker the Docker
socket. Require human Engineering and Release approvals and actual PR evidence.

Run the full static checker separately:

```bash
.venv/bin/python -m switchcrew.deployment_preflight
```

It may correctly report blocked privileged-worker configuration after the core
is ready. Follow [deployment acceptance](../../docs/DEPLOYMENT_ACCEPTANCE.md)
before announcing the full workflow is hosted.

## Operations and recovery

Inspect failures locally with `sudo journalctl -u flip-api -u flip-pm -u
flip-research --since '15 minutes ago'` and `bash deploy/switch/local.sh ps`.
Redact logs before sharing. After configuration fixes, run
`sudo systemctl restart flip-api flip-pm flip-research`.

Use `sudo systemctl stop flip-research flip-pm flip-api flip-switch` for a
non-destructive shutdown. Stopping Switch directly stops dependent native
services; start all four explicitly again afterward. Do not delete volumes or
prune Docker to repair startup failures.

Back up SQLite/checkpoints/execution ledgers with SQLite-aware backups, Switch
PostgreSQL consistently, private configuration, and assignment repositories.
Encrypt copies off the host and test restoration. Copying only a live SQLite
file can miss its WAL. No backup job is installed by this setup.
