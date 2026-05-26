#!/usr/bin/env bash
# Weekly PLATEAU dashboard aggregation batch.
# Phases (DESIGN.md §5): wiki status -> per-region OSM fetch+load -> per-city stats + rollup.
# A flock guards against concurrent runs; compute_stats also takes a DB advisory lock.
# Temp pbf/geojsonseq are removed via trap (disk safety).
#
# Usage:
#   run_batch.sh --postgres-url "$DATABASE_URL" \
#     [--regions "hokkaido tohoku kanto chubu kansai chugoku shikoku kyushu"] \
#     [--csv data/plateau_city_master_2025.csv]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="${DASH_LOCK:-/tmp/dash_batch.lock}"
exec 9>"$LOCK"
flock -n 9 || { echo "another run_batch holds $LOCK; aborting"; exit 1; }

PGURL=""
REGIONS="hokkaido tohoku kanto chubu kansai chugoku shikoku kyushu"
CSV=""
while [ $# -gt 0 ]; do
  case "$1" in
    --postgres-url) PGURL="$2"; shift 2;;
    --regions)      REGIONS="$2"; shift 2;;
    --csv)          CSV="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
done
[ -n "$PGURL" ] || { echo "--postgres-url required"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== Phase 0: city master =="
if [ -n "$CSV" ]; then
  python3 "$HERE/ingest/load_city_master.py" "$CSV" --postgres-url "$PGURL"
else
  echo "(skipped: no --csv)"
fi

echo "== Phase 1: OSM wiki status =="
python3 "$HERE/ingest/parse_wiki_imports.py" --postgres-url "$PGURL"

echo "== Phase 2-3: per-region OSM buildings =="
DISK() { df -h / | awk 'NR==2{print $4" free, "$5" used"}'; }   # disk footprint logging (see DESIGN §13)
for r in $REGIONS; do
  GJ="$WORK/$r.geojsonseq"
  echo "[disk] $r start:           $(DISK)"
  "$HERE/osmium/fetch_region_buildings.sh" "$r" "$GJ" "$WORK"
  echo "[disk] $r after fetch ($(du -h "$GJ" 2>/dev/null | cut -f1) geojsonseq): $(DISK)"
  python3 "$HERE/ingest/load_osm_buildings.py" "$GJ" --postgres-url "$PGURL"
  rm -f "$GJ"
  echo "[disk] $r after load+clean: $(DISK)"
done

echo "== Phase 4-5: stats + rollup =="
python3 "$HERE/ingest/compute_stats.py" --postgres-url "$PGURL"

echo "== batch done =="
