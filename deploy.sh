#!/usr/bin/env bash
# Deploy the Jolto web app via docker compose.
#
# Safe to run repeatedly. Pulls latest code, rebuilds the image, recreates
# the running container, waits for /healthz, and tails the last log lines.
#
# Usage:
#   ./deploy.sh               # standard deploy
#   ./deploy.sh --no-pull     # skip git pull (useful during local testing)
#   ./deploy.sh --no-cache    # full rebuild (use after pyproject.toml change)
#   ./deploy.sh --tail 100    # show more log lines after deploy

set -euo pipefail

cd "$(dirname "$0")"

PULL=true
NO_CACHE=false
TAIL=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-pull)  PULL=false;      shift ;;
    --no-cache) NO_CACHE=true;   shift ;;
    --tail)     TAIL="$2";       shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "unknown arg: $1" >&2
      exit 1 ;;
  esac
done

log() { printf '\n[deploy] %s\n' "$*"; }
ok()  { printf '[deploy] ok: %s\n' "$*"; }

# Resolve host-side port from .env if present, else fall back to 9000.
HOST_PORT=9000
if [[ -f .env ]]; then
  parsed=$(grep -E '^HOST_PORT=' .env | cut -d= -f2 || true)
  if [[ -n "${parsed:-}" ]]; then HOST_PORT="$parsed"; fi
fi

# Preflight sanity checks.
for bin in docker git; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing: $bin" >&2; exit 1; }
done
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "warning: .env not found; API keys and AUTH_* must be set elsewhere" >&2
fi

# ── 1. Code ──────────────────────────────────────────────
if $PULL; then
  log "Pulling latest code"
  git pull --ff-only
  ok "repo at $(git rev-parse --short HEAD)"
else
  log "Skipping git pull"
fi

# ── 2. Build ─────────────────────────────────────────────
log "Building image"
if $NO_CACHE; then
  docker compose build --no-cache --pull
else
  docker compose build
fi
ok "build complete"

# ── 3. Recreate container ────────────────────────────────
log "Starting container"
docker compose up -d --remove-orphans
ok "container started"

# ── 4. Wait for /healthz on host port ────────────────────
log "Waiting for /healthz on 127.0.0.1:${HOST_PORT}"
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
    ok "healthy after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
printf '\n'

# Verify final state.
if ! curl -sf "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
  echo "[deploy] ERROR: /healthz did not respond in time" >&2
  echo "[deploy] last logs:" >&2
  docker compose logs --tail 50 jolto >&2
  exit 2
fi

# ── 5. Summary ───────────────────────────────────────────
log "Recent logs"
docker compose logs --tail "$TAIL" jolto

cat <<EOF

[deploy] done.

  Public:     https://jolto-ai.jeenius.tech
  Healthz:    curl https://jolto-ai.jeenius.tech/healthz
  Live logs:  docker compose logs -f jolto
  Shell:      docker compose exec jolto bash
  Last run:   curl -u USER:PASS https://jolto-ai.jeenius.tech/api/history | jq

EOF
