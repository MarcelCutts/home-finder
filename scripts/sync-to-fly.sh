#!/usr/bin/env bash
set -euo pipefail

# Replacing a running database can erase owner decisions or detached WAL writes.
cat >&2 <<'MESSAGE'
Database file replacement is disabled.
Create a consistent backup and restore into a NEW isolated directory:
  uv run python -m home_finder.pipeline.backup --help
See docs/operations.md for migration and restore verification.
Keep collection and dashboard writes on the same authoritative database.
MESSAGE
exit 2
