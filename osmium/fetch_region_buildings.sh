#!/usr/bin/env bash
# Fetch a Geofabrik Japan region extract and extract OSM building polygons to GeoJSONSeq.
# Low-memory pipeline (DESIGN.md §3.1/§11): osmium export (disk index) | grep building.
# Does NOT use osmium tags-filter (near-OOM on a low-memory host, RAM ~1GB).
#
# Incremental download (opt-in, DESIGN.md §9-4): set DASH_PBF_CACHE to a directory
# AND install osmupdate (osmctools). The region pbf is then kept there and brought
# up to date with replication diffs instead of re-downloading the full extract each
# run. Without DASH_PBF_CACHE (or without osmupdate) it falls back to a full download,
# i.e. the default batch behaviour is unchanged.
#   NOTE: only the *download* is incremental — export+load still process the whole
#   region each run. (True per-building incremental load is a larger change.)
#
# Usage: fetch_region_buildings.sh <region> <out.geojsonseq> [workdir]
#   region e.g. shikoku, kanto, kansai, ... (Geofabrik asia/japan/<region>-*)
set -euo pipefail

REGION="$1"; OUT="$2"; WORK="${3:-$(mktemp -d)}"
mkdir -p "$WORK"
FULL_URL="https://download.geofabrik.de/asia/japan/${REGION}-latest.osm.pbf"
UPDATE_URL="https://download.geofabrik.de/asia/japan/${REGION}-updates/"
CACHE_DIR="${DASH_PBF_CACHE:-}"

TMP_PBF="$WORK/${REGION}.osm.pbf"
NEW_PBF="$WORK/${REGION}.new.osm.pbf"
cleanup() { rm -f "$TMP_PBF" "$NEW_PBF"; }   # never deletes the persistent cache pbf
trap cleanup EXIT

t=$SECONDS
if [ -n "$CACHE_DIR" ] && command -v osmupdate >/dev/null 2>&1 && [ -f "$CACHE_DIR/${REGION}.osm.pbf" ]; then
  echo "[fetch] incremental: osmupdate cached ${REGION}.osm.pbf"
  if osmupdate "$CACHE_DIR/${REGION}.osm.pbf" "$NEW_PBF" --base-url="$UPDATE_URL" -v 2>&1 | tail -3; then
    mv -f "$NEW_PBF" "$CACHE_DIR/${REGION}.osm.pbf"
  else
    echo "[fetch] osmupdate failed -> full download"
    curl -sL --fail --max-time 1800 -o "$CACHE_DIR/${REGION}.osm.pbf" "$FULL_URL"
  fi
  PBF="$CACHE_DIR/${REGION}.osm.pbf"
elif [ -n "$CACHE_DIR" ]; then
  mkdir -p "$CACHE_DIR"
  echo "[fetch] full download -> cache (next run incremental once osmupdate is installed)"
  curl -sL --fail --max-time 1800 -o "$CACHE_DIR/${REGION}.osm.pbf" "$FULL_URL"
  PBF="$CACHE_DIR/${REGION}.osm.pbf"
else
  echo "[fetch] full download $FULL_URL"
  curl -sL --fail --max-time 1800 -o "$TMP_PBF" "$FULL_URL"
  PBF="$TMP_PBF"
fi
echo "[time] fetch: $((SECONDS - t))s ($(ls -lh "$PBF" | awk '{print $5}'))"

echo "[fetch] osmium export | grep building -> $OUT"
t=$SECONDS
osmium export "$PBF" --add-unique-id=type_id --index-type=sparse_file_array \
  --geometry-types=polygon -f geojsonseq -o - | grep '"building":' > "$OUT"
echo "[time] export|grep: $((SECONDS - t))s ($(wc -l < "$OUT") features)"
