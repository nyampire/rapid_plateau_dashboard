#!/usr/bin/env bash
# PoC: extract OSM buildings for one city from a regional pbf and export to GeoJSONSeq.
# Usage: run_city_osmium.sh <city_code> <region_pbf> <minlon> <minlat> <maxlon> <maxlat>
set -euo pipefail

CITY="$1"; PBF="$2"; MINLON="$3"; MINLAT="$4"; MAXLON="$5"; MAXLAT="$6"
DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="$DIR/work_$CITY"
mkdir -p "$WORK"

# macOS: /usr/bin/time -l prints peak RSS. Linux would use -v.
TIME=/usr/bin/time

echo "=== [$CITY] osmium extract (bbox clip, complete_ways) ==="
$TIME -l osmium extract -b "$MINLON,$MINLAT,$MAXLON,$MAXLAT" "$PBF" \
  -o "$WORK/clip.osm.pbf" --overwrite 2>"$WORK/t_extract.txt"
grep "maximum resident" "$WORK/t_extract.txt" || true
grep "real" "$WORK/t_extract.txt" || true

echo "=== [$CITY] osmium tags-filter (nwr/building) ==="
$TIME -l osmium tags-filter "$WORK/clip.osm.pbf" nwr/building \
  -o "$WORK/buildings.osm.pbf" --overwrite 2>"$WORK/t_filter.txt"
grep "maximum resident" "$WORK/t_filter.txt" || true

echo "=== [$CITY] osmium export (geojsonseq, sparse_file_array index) ==="
$TIME -l osmium export "$WORK/buildings.osm.pbf" \
  --add-unique-id=type_id \
  --index-type=sparse_file_array \
  --geometry-types=polygon \
  -f geojsonseq -o "$WORK/buildings.geojsonseq" --overwrite 2>"$WORK/t_export.txt"
grep "maximum resident" "$WORK/t_export.txt" || true

echo "=== [$CITY] feature count ==="
wc -l < "$WORK/buildings.geojsonseq"
ls -lh "$WORK/buildings.geojsonseq"
