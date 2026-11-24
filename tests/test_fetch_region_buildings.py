"""Tests for osmium/fetch_region_buildings.sh's download fallback (no network, no osmium).

curl and osmium are replaced with fake scripts placed first on PATH. The fake curl
records each URL it is asked for and succeeds only for the URLs listed in
FAKE_CURL_OK, so a test can make <region>-latest.osm.pbf fail the way Geofabrik's
does when latest points at a dated extract that is not published yet.
"""
import datetime
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "osmium" / "fetch_region_buildings.sh"
BASE = "https://download.geofabrik.de/asia/japan/hokkaido/hokkaido-"

FAKE_CURL = """#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    --retry|--retry-delay|--max-time) shift 2;;
    -*) shift;;
    *) url="$1"; shift;;
  esac
done
echo "$url" >> "$FAKE_CURL_LOG"
for ok in $FAKE_CURL_OK; do
  if [ "$url" = "$ok" ]; then echo pbf > "$out"; exit 0; fi
done
exit 22
"""

FAKE_OSMIUM = """#!/usr/bin/env bash
echo '{"type":"Feature","properties":{"building":"yes"}}'
"""


def _days_ago(n):
    d = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=n)
    return d.strftime("%y%m%d")


def _run(tmp_path, ok_urls):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("curl", FAKE_CURL), ("osmium", FAKE_OSMIUM)):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)
    log = tmp_path / "curl.log"
    log.touch()
    env = {k: v for k, v in os.environ.items() if k != "DASH_PBF_CACHE"}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        FAKE_CURL_LOG=str(log),
        FAKE_CURL_OK=" ".join(ok_urls),
    )
    out = tmp_path / "out.geojsonseq"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "hokkaido", str(out), str(tmp_path / "work")],
        env=env, capture_output=True, text=True,
    )
    return proc, log.read_text().split(), out


def test_uses_latest_when_it_is_available(tmp_path):
    proc, urls, out = _run(tmp_path, [BASE + "latest.osm.pbf"])
    assert proc.returncode == 0, proc.stderr
    assert urls == [BASE + "latest.osm.pbf"]
    assert out.read_text().count('"building":') == 1


def test_falls_back_to_previous_days_when_latest_fails(tmp_path):
    two_days_ago = BASE + _days_ago(2) + ".osm.pbf"
    proc, urls, out = _run(tmp_path, [two_days_ago])
    assert proc.returncode == 0, proc.stderr
    assert urls == [
        BASE + "latest.osm.pbf",
        BASE + _days_ago(1) + ".osm.pbf",
        two_days_ago,
    ]
    assert out.read_text().count('"building":') == 1


def test_fails_when_no_recent_extract_exists(tmp_path):
    proc, urls, _ = _run(tmp_path, [])
    assert proc.returncode != 0
    assert urls == [BASE + "latest.osm.pbf"] + [
        BASE + _days_ago(n) + ".osm.pbf" for n in (1, 2, 3)
    ]
