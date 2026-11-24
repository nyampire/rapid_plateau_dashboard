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
# Resilience: the full-download curl retries transient Geofabrik HTTP errors
# (--retry 3 --retry-delay 10 --retry-all-errors). A single momentary 4xx/5xx on
# one region used to abort the whole weekly batch (set -e), losing that week's
# snapshot; the retry absorbs the hiccup so the batch keeps its all-or-nothing
# consistency without being fragile to a one-off blip.
#
# Fallback to dated extracts: <region>-latest.osm.pbf redirects to the newest dated
# extract (<region>-YYMMDD.osm.pbf), and since Geofabrik moved the Japan regions into
# per-region directories (2026-11) that file is often still 404 for about a day after
# the redirect starts pointing at it. When latest fails, the full download tries the
# dated extracts of the previous FALLBACK_DAYS days (UTC dates), newest first.
#
# Usage: fetch_region_buildings.sh <region> <out.geojsonseq> [workdir]
#   region e.g. shikoku, kanto, kansai, ... (Geofabrik asia/japan/<region>/<region>-*)
set -euo pipefail

REGION="$1"; OUT="$2"; WORK="${3:-$(mktemp -d)}"
mkdir -p "$WORK"
FULL_URL="https://download.geofabrik.de/asia/japan/${REGION}/${REGION}-latest.osm.pbf"
UPDATE_URL="https://download.geofabrik.de/asia/japan/${REGION}/updates/"
DATED_URL_PREFIX="https://download.geofabrik.de/asia/japan/${REGION}/${REGION}-"
FALLBACK_DAYS=3
CACHE_DIR="${DASH_PBF_CACHE:-}"

# UTC date N days ago as YYMMDD (GNU date on the server, BSD date on macOS).
days_ago() { date -u -d "$1 days ago" +%y%m%d 2>/dev/null || date -u -v-"$1"d +%y%m%d; }

# Full download of the region extract to $1: latest first, then the dated fallback.
fetch_full() {
  local dest="$1" n url
  if curl -sL --fail --retry 3 --retry-delay 10 --retry-all-errors --max-time 1800 -o "$dest" "$FULL_URL"; then
    return 0
  fi
  for n in $(seq 1 "$FALLBACK_DAYS"); do
    url="${DATED_URL_PREFIX}$(days_ago "$n").osm.pbf"
    echo "[fetch] latest unavailable -> $url"
    if curl -sL --fail --retry 3 --retry-delay 10 --max-time 1800 -o "$dest" "$url"; then
      return 0
    fi
  done
  echo "[fetch] no ${REGION} extract from the last ${FALLBACK_DAYS} days" >&2
  return 1
}

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
    fetch_full "$CACHE_DIR/${REGION}.osm.pbf"
  fi
  PBF="$CACHE_DIR/${REGION}.osm.pbf"
elif [ -n "$CACHE_DIR" ]; then
  mkdir -p "$CACHE_DIR"
  echo "[fetch] full download -> cache (next run incremental once osmupdate is installed)"
  fetch_full "$CACHE_DIR/${REGION}.osm.pbf"
  PBF="$CACHE_DIR/${REGION}.osm.pbf"
else
  echo "[fetch] full download $FULL_URL"
  fetch_full "$TMP_PBF"
  PBF="$TMP_PBF"
fi
echo "[time] fetch: $((SECONDS - t))s ($(ls -lh "$PBF" | awk '{print $5}'))"

echo "[fetch] osmium export | grep building -> $OUT"
t=$SECONDS
osmium export "$PBF" --add-unique-id=type_id --index-type=sparse_file_array \
  --geometry-types=polygon -f geojsonseq -o - | grep '"building":' > "$OUT"
echo "[time] export|grep: $((SECONDS - t))s ($(wc -l < "$OUT") features)"
