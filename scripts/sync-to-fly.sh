#!/usr/bin/env bash
set -euo pipefail

APP="home-finder"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data"
FULL_SYNC=false

# Clean up local temp files on exit
cleanup() { rm -f /tmp/hf_db.tar.gz /tmp/hf_all_images.tar.gz /tmp/hf_new_images.tar.gz; }
trap cleanup EXIT

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) FULL_SYNC=true; shift ;;
    *) echo "Usage: $0 [--full]"; exit 1 ;;
  esac
done

# Get machine ID non-interactively
MACHINE_ID=$(fly machine list --app "$APP" --json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
echo "    Machine: $MACHINE_ID"

echo "==> Checkpointing local WAL..."
python3 -c "
import sqlite3
c = sqlite3.connect('$DATA_DIR/properties.db')
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.close()
print('    WAL flushed to main DB')
"

# Machine should already be running (it's the web dashboard).
# Verify SSH is reachable before transferring.
echo "==> Checking SSH readiness..."
for i in {1..10}; do
  if fly ssh console --app "$APP" -C "echo ok" >/dev/null 2>&1; then
    echo "    SSH ready"
    break
  fi
  if [ "$i" -eq 10 ]; then
    echo "    SSH not reachable after 20s — is the machine running?"
    exit 1
  fi
  sleep 2
done

# --- Database sync ---
# fly sftp put refuses to overwrite existing files (flyctl v0.3.177+, no --force flag).
# Workaround: rm the remote file first, then upload.
echo "==> Replacing database..."
echo "    Removing old DB on remote..."
fly ssh console --app "$APP" -C "rm -f /app/data/properties.db /app/data/properties.db-shm /app/data/properties.db-wal"
echo "    Uploading..."
fly sftp put "$DATA_DIR/properties.db" /app/data/properties.db --app "$APP" -q
echo "    Setting ownership..."
fly ssh console --app "$APP" -C "chown appuser:appuser /app/data/properties.db"

# --- Image sync (tar pattern — needed for directories) ---
if [ "$FULL_SYNC" = true ]; then
  echo "==> Syncing image cache (full replacement)..."
  cd "$DATA_DIR/image_cache"
  tar czf /tmp/hf_all_images.tar.gz --no-xattrs --no-mac-metadata .
  SIZE=$(du -h /tmp/hf_all_images.tar.gz | cut -f1)
  echo "    Compressed size: $SIZE"

  fly ssh console --app "$APP" -C "mkdir -p /app/data/image_cache"
  echo "    Uploading tarball via sftp..."
  fly ssh console --app "$APP" -C "rm -f /app/data/hf_all_images.tar.gz" 2>/dev/null || true
  fly sftp put /tmp/hf_all_images.tar.gz /app/data/hf_all_images.tar.gz --app "$APP" -q

  echo "    Extracting on remote..."
  fly ssh console --app "$APP" -C "sh -c 'cd /app/data/image_cache && tar xzf /app/data/hf_all_images.tar.gz && rm /app/data/hf_all_images.tar.gz'"
else
  echo "==> Syncing image cache (incremental — use --full to sync updates to existing properties)..."
  REMOTE_DIRS=$(fly ssh console --app "$APP" -C "ls /app/data/image_cache/ 2>/dev/null" 2>/dev/null | tr -d '\r' || echo "")
  LOCAL_DIRS=$(ls "$DATA_DIR/image_cache/")
  NEW_DIRS=$(comm -23 <(echo "$LOCAL_DIRS" | sort) <(echo "$REMOTE_DIRS" | sort) || true)

  if [ -z "$NEW_DIRS" ]; then
    echo "    Image cache is up to date (no new directories)."
  else
    COUNT=$(echo "$NEW_DIRS" | wc -l | tr -d ' ')
    echo "    Uploading $COUNT new property directories..."

    cd "$DATA_DIR/image_cache"
    # shellcheck disable=SC2086
    tar czf /tmp/hf_new_images.tar.gz --no-xattrs --no-mac-metadata $NEW_DIRS
    SIZE=$(du -h /tmp/hf_new_images.tar.gz | cut -f1)
    echo "    Compressed size: $SIZE"

    echo "    Uploading tarball via sftp..."
    fly ssh console --app "$APP" -C "rm -f /app/data/hf_new_images.tar.gz" 2>/dev/null || true
    fly sftp put /tmp/hf_new_images.tar.gz /app/data/hf_new_images.tar.gz --app "$APP" -q

    echo "    Extracting on remote..."
    fly ssh console --app "$APP" -C "sh -c 'mkdir -p /app/data/image_cache && cd /app/data/image_cache && tar xzf /app/data/hf_new_images.tar.gz && rm /app/data/hf_new_images.tar.gz'"
  fi
fi

echo "==> Restarting app to pick up new data..."
fly machine restart "$MACHINE_ID" --app "$APP"

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
