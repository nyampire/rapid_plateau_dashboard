#!/usr/bin/env bash
# Download a Geofabrik Japan region pbf and extract OSM building polygons to GeoJSONSeq.
# Low-memory pipeline (DESIGN.md §3.1/§11): osmium export (disk index) | grep building.
# Does NOT use osmium tags-filter (near-OOM on a low-memory host, RAM ~1GB).
#
# Usage: fetch_region_buildings.sh <region> <out.geojsonseq> [workdir]
#   region e.g. shikoku, kanto, kansai, ... (Geofabrik asia/japan/<region>-latest.osm.pbf)
set -euo pipefail

REGION="$1"; OUT="$2"; WORK="${3:-$(mktemp -d)}"
mkdir -p "$WORK"
PBF="$WORK/${REGION}.osm.pbf"
URL="https://download.geofabrik.de/asia/japan/${REGION}-latest.osm.pbf"

cleanup() { rm -f "$PBF"; }
trap cleanup EXIT

echo "[fetch] downloading $URL"
t=$SECONDS
curl -sL --fail --max-time 1800 -o "$PBF" "$URL"
echo "[time] download: $((SECONDS - t))s ($(ls -lh "$PBF" | awk '{print $5}'))"

echo "[fetch] osmium export | grep building -> $OUT"
t=$SECONDS
osmium export "$PBF" --add-unique-id=type_id --index-type=sparse_file_array \
  --geometry-types=polygon -f geojsonseq -o - | grep '"building":' > "$OUT"
echo "[time] export|grep: $((SECONDS - t))s ($(wc -l < "$OUT") features)"
