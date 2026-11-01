# SwitchCrew

SwitchCrew is a Python 3.12/FastAPI service with SQLite task history and separate
PM and Research workers communicating through Switch's Discord text bridge.
This slice implements the nine Day 1 workstreams from the supplied checklist.
A real Discord → PM → Research → original-thread exchange and several restart,
disconnect and injected-gap checks were recorded on the verification Mac on
November 1. See the [evidence and remaining gates](deploy/switch/VERIFICATION.md)
and [recovery acceptance runbook](deploy/switch/RECOVERY_ACCEPTANCE.md). Deployment
credentials and running services are local to that Mac, not supplied by Git.

All 15 specified agents now have independent LangGraph scaffolds. PM and Research
run their existing work through compiled graphs with SQLite checkpoints. Optional
Waggle MCP memory provides project recall and curated outcome storage; a generic
Switch runtime connects each additional specialist under its own identity. See
[agent workflows](docs/AGENT_WORKFLOWS.md), [memory](docs/MEMORY.md), and
[Switch runtimes](docs/AGENT_SWITCH.md).

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --frozen
# First setup only: preserve an existing .env and its credentials.
test -e .env || cp .env.example .env
chmod 600 .env
# Generate a token; paste it into SWITCHCREW_API_TOKEN in .env.
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
uv run uvicorn switchcrew.api:create_app --factory --host 127.0.0.1 --no-access-log
```

`GET /health` is public. Task APIs require `Authorization: Bearer <token>`.
Keep `.env`, SQLite files, and registration key files out of Git. A local macOS
runtime that marks editable `.pth` files hidden may need `uv sync --frozen
--no-editable` before running the installed commands; tests use `src` directly.

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests scripts
uv build
```

See [local setup and API documentation](docs/OPERATIONS.md),
[Switch/Discord provisioning](deploy/switch/README.md), and
[implementation and review status](docs/DAY1_STATUS.md).

## Run in Docker

After configuring `.env`:

```sh
docker compose up --build -d api
docker compose -f compose.yaml -f deploy/compose.switch-local.yaml \
  -f deploy/switch/worker-recovery.yaml --profile workers up --build -d
```

The API listens on `127.0.0.1:8000`. All three application processes use the
`crew-data` named volume. Switch runs separately with its own PostgreSQL. Workers
need a reachable agent bridge URL; container `127.0.0.1` points at the container.
Use a secured external URL or attach workers to the actual Switch Docker network
with hostname `switch` and port 8000. The provisioning guide includes a local
network override and avoids host port conflicts.
For the pinned local Switch server and gateway, run `bash deploy/switch/local.sh`.
The gateway uses `http://127.0.0.1:3001`, and Switch uses port 8080. See the
[deployment guide](deploy/switch/README.md) for Console and local credentials.
The worker recovery override restarts failures but leaves an evicted worker
stopped. Start workers explicitly after restarting Docker; remove the local
network override when using a separately configured remote Switch server.

## Scope

Research retrieves bounded excerpts from operator-configured public HTTPS sources.
It does not implement autonomous web search or claim acceptance criteria are
verified. Missing criteria trigger a clarification response; send a new complete
JSON request. [Approved Engineering and Release](docs/APPROVAL_RELEASE.md) now
connect PM to the Engineering worker, independent revision-pinned QA, human
release approval/rejection, and GitHub draft PR publication with an audit receipt.
The route requires explicit identities, operator job templates and human approvers;
unconfigured requests cannot execute Engineering or publish. Controlled local
validation covers this pipeline; real Console-managed Codex and GitHub acceptance
still need the verification host and private configuration. Discord voice and
community workflows remain later work. Memory requires explicit configuration.

Work began October 29, 2026 at the user's explicit request. The earlier
[preparation notes](docs/PREPARATION.md) and [build plan](docs/BUILD_PLAN.md) remain
as dated history. The competition rules reserve Switch workflow work for
November 4–7; this earlier implementation must not be represented as event-built.
The verbatim supplied [specification](docs/SPECIFICATION.md) is preserved.
