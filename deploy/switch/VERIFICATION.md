# Local deployment verification — October 29, 2026

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
