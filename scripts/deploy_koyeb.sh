#!/usr/bin/env bash
# Deploy OtelMind services to Koyeb.
# Prerequisites: koyeb CLI installed and authenticated.
# Environment variables: NEON_DATABASE_URL, KOYEB_APP_NAME (default: otelmind)

set -euo pipefail

APP_NAME="${KOYEB_APP_NAME:-otelmind}"
REGION="${KOYEB_REGION:-fra}"
INSTANCE_TYPE="${KOYEB_INSTANCE_TYPE:-nano}"

echo "==> Deploying OtelMind API to Koyeb (app=$APP_NAME)"

koyeb app create "$APP_NAME" 2>/dev/null || true

# --- API service ---
koyeb service create "$APP_NAME/api" \
  --docker "ghcr.io/${GITHUB_REPOSITORY:-otelmind/otelmind}:latest" \
  --docker-command "python -m otelmind.api.main" \
  --instance-type "$INSTANCE_TYPE" \
  --regions "$REGION" \
  --ports "8000:http" \
  --routes "/:8000" \
  --env "DATABASE_URL=${NEON_DATABASE_URL}" \
  --env "DATABASE_URL_SYNC=${NEON_DATABASE_URL_SYNC:-$NEON_DATABASE_URL}" \
  --env "API_HOST=0.0.0.0" \
  --env "API_PORT=8000" \
  --env "WATCHDOG_INTERVAL_SECONDS=60" \
  --checks "8000:http:/api/v1/health:60"

echo "==> API service deployed."

# --- Watchdog service ---
koyeb service create "$APP_NAME/watchdog" \
  --docker "ghcr.io/${GITHUB_REPOSITORY:-otelmind/otelmind}:latest" \
  --docker-command "python -c 'import asyncio; from otelmind.watchdog.watchdog_agent import run_watchdog; asyncio.run(run_watchdog())'" \
  --instance-type "$INSTANCE_TYPE" \
  --regions "$REGION" \
  --env "DATABASE_URL=${NEON_DATABASE_URL}" \
  --env "DATABASE_URL_SYNC=${NEON_DATABASE_URL_SYNC:-$NEON_DATABASE_URL}" \
  --env "WATCHDOG_INTERVAL_SECONDS=60"

echo "==> Watchdog service deployed."
echo "==> Done. Check status: koyeb service list --app $APP_NAME"
