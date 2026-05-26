#!/usr/bin/env bash
# Low-memory building extraction: osmium export (disk index) | grep building. No tags-filter.
# Usage: measure_export.sh <pbf> <tag>
set -uo pipefail
DIR="${POC_MEM_DIR:-/tmp/poc_mem}"; mkdir -p "$DIR"; cd "$DIR"
PBF="$1"; TAG="${2:-region}"
SAMP=""
cleanup(){ [ -n "$SAMP" ] && kill "$SAMP" 2>/dev/null; rm -f "$DIR/be_${TAG}.geojsonseq" "$DIR/sampler_${TAG}.peak"; }
trap cleanup EXIT
swapused(){ awk '/^SwapTotal/{t=$2}/^SwapFree/{f=$2}END{print t-f}' /proc/meminfo; }
memavail(){ awk '/^MemAvailable/{print $2}' /proc/meminfo; }
BASE_SWAP=$(swapused)
echo "baseline: MemAvailable=$(memavail)kB SwapUsed=${BASE_SWAP}kB"
( ms=0; ma=99999999; while :; do s=$(swapused); a=$(memavail); [ "$s" -gt "$ms" ]&&ms=$s; [ "$a" -lt "$ma" ]&&ma=$a; echo "$ms $ma">"$DIR/sampler_${TAG}.peak"; sleep 1; done ) & SAMP=$!
echo "=== osmium export (sparse_file_array) | grep building ==="
/usr/bin/time -v bash -c "osmium export '$PBF' --add-unique-id=type_id --index-type=sparse_file_array --geometry-types=polygon -f geojsonseq -o - | grep '\"building\"' > 'be_${TAG}.geojsonseq'" 2> "tee_${TAG}.txt"
grep -E "Maximum resident|Elapsed \(wall|Major" "tee_${TAG}.txt" | sed 's/^[[:space:]]*/  /'
echo "  building features: $(wc -l < be_${TAG}.geojsonseq)"
kill "$SAMP" 2>/dev/null; SAMP=""
read PS MA < "$DIR/sampler_${TAG}.peak"
echo "=== summary [$TAG export|grep] ==="
echo "  peak SwapUsed=${PS}kB (baseline ${BASE_SWAP}kB, delta=$((PS-BASE_SWAP))kB)"
echo "  min MemAvailable=${MA}kB"
