#!/usr/bin/env bash
set -euo pipefail

# Retired deliberately, including --full. Do not add an override that can replace
# an active database or remove its WAL/SHM while dashboard writers are running.
cat >&2 <<'MESSAGE'
Database file replacement is disabled: it can erase dashboard decisions and history.
Use the consistent backup/isolated restore commands and PR05 authority runbook:
  uv run python -m home_finder.pipeline.backup --help
  docs/plans/2026-09-28-pr05a-backup-and-authority.md
Collectors must write to the chosen authority or its future observation interface.
MESSAGE
exit 2
