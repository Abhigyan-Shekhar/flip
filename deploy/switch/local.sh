#!/usr/bin/env bash
# Reproducible local Switch stack. Secrets/source stay in ignored .switch/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER="$ROOT/.switch/server"
REVISION=557b62b23002548baa678339a97c8140c1ccaa8b
ACTION="${1:-up}"
case "$ACTION" in up|ps|stop|logs) ;; *) echo "Usage: $0 [up|ps|stop|logs]" >&2; exit 2 ;; esac
command -v docker >/dev/null 2>&1 || {
  echo "Docker CLI is unavailable. Install/start Docker Desktop before deploying." >&2; exit 1;
}
docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 || {
  echo "Docker Engine or Compose is unavailable. Start Docker Desktop before deploying." >&2; exit 1;
}
if [[ "$ACTION" == up ]]; then
  umask 077
  mkdir -p "$ROOT/.switch"
  if [[ ! -d "$SERVER/.git" ]]; then
    git clone https://github.com/sandbox-quantum/switch.git "$SERVER"
    git -C "$SERVER" checkout --detach "$REVISION"
  fi
  [[ "$(git -C "$SERVER" rev-parse HEAD)" == "$REVISION" ]] || {
    echo "Switch checkout differs from the required revision; refusing to deploy." >&2; exit 1;
  }
  if [[ ! -f "$SERVER/.env" ]]; then
    cp "$SERVER/.env.example" "$SERVER/.env"
    chmod 600 "$SERVER/.env"
    bash "$SERVER/deploy/local/generate-env-secrets.sh" "$SERVER/.env" local
    python3 - "$SERVER/.env" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
values = {
    "SWITCH_VERSION": "local-557b62b",
    "API_HOST_PORT": "8080",
    "GATEWAY_HOST_PORT": "3001",
    "POSTGRES_HOST_PORT": "5433",
    "SWITCH_BIND_ADDR": "127.0.0.1",
    "FRONTEND_BASE_URL": "http://127.0.0.1:3001",
    "GATEWAY_PUBLIC_URL": "http://127.0.0.1:3001",
    "TELEMETRY_ENABLED": "false",
}
lines = path.read_text().splitlines()
lines = [line for line in lines if line.split("=", 1)[0] not in values]
path.write_text("\n".join(lines + [f"{k}={v}" for k, v in values.items()]) + "\n")
PY
  fi
fi
COMPOSE=(docker compose -p switch-server --project-directory "$SERVER"
  -f "$SERVER/deploy/local/standalone-docker-compose.yml"
  -f "$SERVER/deploy/local/standalone-docker-compose.build.yml"
  -f "$ROOT/deploy/switch/restart.yaml" --profile gateway)
if [[ "$ACTION" == up ]]; then
  "${COMPOSE[@]}" up -d --build --wait --wait-timeout 180
  echo 'Switch API: http://127.0.0.1:8080 | Gateway: http://127.0.0.1:3001'
else
  "${COMPOSE[@]}" "$ACTION"
fi
