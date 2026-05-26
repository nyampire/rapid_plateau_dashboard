#!/usr/bin/env bash
# Thin runner for the weekly dashboard batch, intended as the systemd service
# ExecStart (see deploy/rapid-plateau-dashboard.service). Keeps host-specific
# config out of the unit file — everything comes from the environment.
#
# Environment (see deploy/dashboard-batch.env.example):
#   DATABASE_URL  (required)  passed to run_batch.sh
#   DASH_HOME     (optional)  repo root; defaults to this script's parent dir
#   DASH_VENV     (optional)  venv to activate so python3 has psycopg2 etc.
#   DASH_CSV      (optional)  city-master CSV to refresh on each run (Phase 0)
#   DASH_REGIONS  (optional)  space-separated Geofabrik regions (default: all 8)
#
# run_batch.sh already holds an flock, so overlapping triggers are safe.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$HERE")}"

if [ -n "${DASH_VENV:-}" ]; then
  # shellcheck disable=SC1091
  source "$DASH_VENV/bin/activate"
fi

: "${DATABASE_URL:?DATABASE_URL is required (set it in the EnvironmentFile)}"

args=(--postgres-url "$DATABASE_URL")
[ -n "${DASH_CSV:-}" ]     && args+=(--csv "$DASH_CSV")
[ -n "${DASH_REGIONS:-}" ] && args+=(--regions "$DASH_REGIONS")

echo "[run_weekly] starting batch at $(date -Is)"
exec "$DASH_HOME/run_batch.sh" "${args[@]}"
