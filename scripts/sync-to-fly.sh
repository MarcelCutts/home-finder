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

echo "==> Replacing database..."
fly ssh console -C "sh -c 'rm -f /app/data/properties.db /app/data/properties.db-shm /app/data/properties.db-wal'" \
  --app "$APP" 2>/dev/null || true
fly sftp put "$DATA_DIR/properties.db" /app/data/properties.db --app "$APP" -q

echo "==> Syncing image cache (incremental)..."
REMOTE_DIRS=$(fly ssh console --app "$APP" -C "ls /app/data/image_cache/" 2>/dev/null | tr -d '\r' || echo "")
LOCAL_DIRS=$(ls "$DATA_DIR/image_cache/")
NEW_DIRS=$(comm -23 <(echo "$LOCAL_DIRS" | sort) <(echo "$REMOTE_DIRS" | sort) || true)

if [ -z "$NEW_DIRS" ]; then
  echo "    Image cache is up to date."
else
  COUNT=$(echo "$NEW_DIRS" | wc -l | tr -d ' ')
  echo "    Uploading $COUNT new property directories..."

  cd "$DATA_DIR/image_cache"
  # shellcheck disable=SC2086
  tar czf /tmp/hf_new_images.tar.gz --no-xattrs --no-mac-metadata $NEW_DIRS
  SIZE=$(du -h /tmp/hf_new_images.tar.gz | cut -f1)
  echo "    Compressed size: $SIZE"

  fly sftp put /tmp/hf_new_images.tar.gz /app/data/hf_new_images.tar.gz --app "$APP" -q
  fly ssh console --app "$APP" -C "sh -c 'cd /app/data/image_cache && tar xzf /app/data/hf_new_images.tar.gz && rm /app/data/hf_new_images.tar.gz'"
  rm /tmp/hf_new_images.tar.gz
fi

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
