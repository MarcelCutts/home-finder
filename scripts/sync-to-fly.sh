#!/usr/bin/env bash
set -euo pipefail

APP="home-finder"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data"

# Get machine ID non-interactively
MACHINE_ID=$(fly machine list --app "$APP" --json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
echo "    Machine: $MACHINE_ID"

echo "==> Stopping app to release DB locks..."
fly machine stop "$MACHINE_ID" --app "$APP" 2>/dev/null || true
sleep 2

echo "==> Starting app for file transfer..."
fly machine start "$MACHINE_ID" --app "$APP"
sleep 3

echo "==> Removing old data on remote..."
fly ssh console -C "sh -c 'rm -rf /app/data/properties.db /app/data/properties.db-shm /app/data/properties.db-wal /app/data/image_cache'" \
  --app "$APP" 2>/dev/null || true

echo "==> Uploading database..."
fly sftp put "$DATA_DIR/properties.db" /app/data/properties.db --app "$APP" -q

echo "==> Uploading image cache..."
fly sftp put "$DATA_DIR/image_cache/" /app/data/image_cache/ -R --app "$APP" -q

echo "==> Restarting app..."
fly apps restart "$APP"

echo "==> Waiting for health check..."
for i in {1..15}; do
  if curl -sf "https://$APP.fly.dev/health" >/dev/null 2>&1; then
    echo "    Healthy!"
    curl -s "https://$APP.fly.dev/health" | python3 -m json.tool
    exit 0
  fi
  sleep 2
done

echo "    Health check didn't pass in 30s — check 'fly logs --app $APP'"
exit 1
