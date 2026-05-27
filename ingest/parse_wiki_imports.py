#!/usr/bin/env python3
"""Parse OSM wiki JA:MLIT_PLATEAU/imports_list and update dash_city_master.

Authoritative source for OSM import completion status (per imports_outline:
"done" == a completion date is recorded in imports_list).

Page structure:
  === <prefecture><city> ===            e.g. '=== 埼玉県新座市 ==='
  全メッシュ YYYY-MM-DD にインポート完了[、YYYY-MM-DD にすべて妥当性検査終了。]
  {| ... table: | mesh || date || user || note(検証済) ... |}

name->city_code mapping uses dash_city_master.(prefecture || city_name).
Each run resets osm_import_* for all cities, then applies the current wiki state
(so a city dropped from the wiki reverts to not_started). Idempotent.

Usage:
  python3 parse_wiki_imports.py --postgres-url "$DATABASE_URL" [--dry-run]
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

import psycopg2

API = "https://wiki.openstreetmap.org/w/api.php"
PAGE = "JA:MLIT_PLATEAU/imports_list"

HEADING_RE = re.compile(r"^===\s*(.+?)\s*===\s*$", re.MULTILINE)
DONE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*にインポート完了")


def fetch_wikitext():
    q = urllib.parse.urlencode({"action": "parse", "page": PAGE,
                                "prop": "wikitext", "format": "json"})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "rapid-plateau-dashboard/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if "error" in data:
        sys.exit(f"wiki API error: {data['error']}")
    return data["parse"]["wikitext"]["*"]


def parse_sections(wt):
    """Yield (heading, status, date_str_or_None, validated) per level-3 section."""
    matches = list(HEADING_RE.finditer(wt))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        body = wt[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(wt)]
        d = DONE_DATE_RE.search(body)
        if d:
            status, date_str = "done", d.group(1)
        elif body.strip():
            status, date_str = "in_progress", None
        else:
            continue
        validated = "妥当性検査" in body  # city-level full-validation signal
        yield heading, status, date_str, validated


def resolve_city_code(heading, lookup):
    """Map a wiki section heading (prefecture+city_name) to a city_code via `lookup`.

    Falls back for designated-city wards: '<...市><ward>区' -> parent '<...市>'.
    Returns None when nothing matches.
    """
    if heading in lookup:
        return lookup[heading]
    m = re.match(r"^(.+?市).+区$", heading)
    if m and m.group(1) in lookup:
        return lookup[m.group(1)]
    return None


def main():
    ap = argparse.ArgumentParser(description="Update dash_city_master from OSM wiki imports_list.")
    ap.add_argument("--postgres-url", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wt = fetch_wikitext()
    sections = list(parse_sections(wt))
    print(f"parsed {len(sections)} sections from wiki")

    conn = psycopg2.connect(args.postgres_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT prefecture, city_name, city_code FROM dash_city_master "
                        "WHERE prefecture IS NOT NULL AND city_name IS NOT NULL;")
            lookup = {(p or "") + (n or ""): c for p, n, c in cur.fetchall()}

        # Aggregate per city_code (a designated city may appear as several ward sections):
        # done beats in_progress; keep max done-date; validated if any.
        agg = {}  # code -> {status, date, validated, headings:[...]}
        unmatched = []
        for heading, status, date_str, validated in sections:
            code = resolve_city_code(heading, lookup)
            if not code:
                unmatched.append((heading, status))
                continue
            a = agg.setdefault(code, {"status": "in_progress", "date": None,
                                      "validated": False, "headings": []})
            a["headings"].append(heading)
            a["validated"] = a["validated"] or validated
            if status == "done":
                a["status"] = "done"
                if date_str and (a["date"] is None or date_str > a["date"]):
                    a["date"] = date_str

        matched = [(code, a["status"], a["date"], a["validated"], "+".join(a["headings"]))
                   for code, a in agg.items()]
        done = sum(1 for r in matched if r[1] == "done")
        print(f"matched {len(matched)} cities / unmatched {len(unmatched)} sections")
        for h, s in unmatched:
            print(f"  UNMATCHED: {h} ({s})")
        print(f"  -> done={done}, in_progress={len(matched) - done}")

        if args.dry_run:
            for code, status, date_str, validated, heading in sorted(matched):
                print(f"  {code} {heading}: {status} date={date_str} validated={validated}")
            print("dry-run: no DB writes")
            return

        with conn, conn.cursor() as cur:
            cur.execute("UPDATE dash_city_master SET osm_import_status='not_started', "
                        "osm_import_date=NULL, osm_validated=FALSE;")
            for code, status, date_str, validated, _ in matched:
                cur.execute("UPDATE dash_city_master SET osm_import_status=%s, "
                            "osm_import_date=%s, osm_validated=%s, updated_at=now() "
                            "WHERE city_code=%s;", (status, date_str, validated, code))
        print(f"updated {len(matched)} cities (done={done})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
