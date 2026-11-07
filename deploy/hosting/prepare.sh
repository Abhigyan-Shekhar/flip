#!/usr/bin/env bash
# Run as the dedicated operator on Ubuntu after installing Docker and uv.
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ "$(uname -s)" == Linux && "$(id -u)" != 0 ]] || {
  echo 'Run on a Linux server as a nonroot operator.' >&2; exit 1;
}
git merge-base --is-ancestor 06e6d780ab8bead1b16d8926ccf10528d5bd2fc8 HEAD || {
  echo 'This checkout must include the validated handoff baseline; do not deploy master.' >&2
  exit 1
}
command -v uv >/dev/null
docker info >/dev/null
docker compose version >/dev/null
uv python install 3.12
uv sync --frozen --python 3.12 --no-editable
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check src tests scripts deploy/hosting
uv build
.venv/bin/python deploy/hosting/hosting.py init
.venv/bin/python deploy/hosting/hosting.py render --user "$(id -un)" --output .switch/systemd
echo 'Preparation passed. Configure real identities and follow deploy/hosting/README.md before starting services.'
