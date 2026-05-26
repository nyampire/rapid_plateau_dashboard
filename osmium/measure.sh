#!/usr/bin/env bash
# Measure osmium tags-filter + export real memory/swap on this host.
# Usage: measure.sh <pbf> <tag>
set -uo pipefail
DIR="${POC_MEM_DIR:-/tmp/poc_mem}"; mkdir -p "$DIR"; cd "$DIR"
PBF="$1"; TAG="${2:-region}"
SAMP=""
cleanup(){ [ -n "$SAMP" ] && kill "$SAMP" 2>/dev/null; rm -f "$DIR/b_${TAG}.osm.pbf" "$DIR/b_${TAG}.geojsonseq" "$DIR/sampler_${TAG}.peak"; }
trap cleanup EXIT

swapused(){ awk '/^SwapTotal/{t=$2}/^SwapFree/{f=$2}END{print t-f}' /proc/meminfo; }
memavail(){ awk '/^MemAvailable/{print $2}' /proc/meminfo; }

BASE_SWAP=$(swapused)
echo "baseline: MemAvailable=$(memavail)kB SwapUsed=${BASE_SWAP}kB  ($(date +%H:%M:%S))"

( ms=0; ma=99999999
  while :; do s=$(swapused); a=$(memavail)
    [ "$s" -gt "$ms" ] && ms=$s; [ "$a" -lt "$ma" ] && ma=$a
    echo "$ms $ma" > "$DIR/sampler_${TAG}.peak"; sleep 1
  done ) & SAMP=$!

echo "=== tags-filter nwr/building ==="
/usr/bin/time -v osmium tags-filter "$PBF" nwr/building -o "b_${TAG}.osm.pbf" --overwrite 2> "tf_${TAG}.txt"
grep -E "Maximum resident|Elapsed \(wall|Major" "tf_${TAG}.txt" | sed 's/^[[:space:]]*/  /'
ls -lh "b_${TAG}.osm.pbf" | awk '{print "  filtered pbf:", $5}'

echo "=== export (sparse_file_array) ==="
/usr/bin/time -v osmium export "b_${TAG}.osm.pbf" --add-unique-id=type_id --index-type=sparse_file_array --geometry-types=polygon -f geojsonseq -o "b_${TAG}.geojsonseq" --overwrite 2> "te_${TAG}.txt"
grep -E "Maximum resident|Elapsed \(wall|Major" "te_${TAG}.txt" | sed 's/^[[:space:]]*/  /'
echo "  features: $(wc -l < b_${TAG}.geojsonseq)"

kill "$SAMP" 2>/dev/null; SAMP=""
read PS MA < "$DIR/sampler_${TAG}.peak"
echo "=== summary [$TAG] ==="
echo "  peak SwapUsed=${PS}kB (baseline ${BASE_SWAP}kB, delta=$((PS-BASE_SWAP))kB)"
echo "  min MemAvailable during run=${MA}kB"
