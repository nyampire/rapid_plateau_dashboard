#!/usr/bin/env python3
"""Load the national PLATEAU city master CSV into dash_city_master.

CSV columns: city_code,prefecture,region,city_name,building_lods,spec_versions
(produced by extract_city_master.py from attributedata_2025 Excel.)

Upsert updates only master-derived columns; OSM-wiki columns (osm_import_*) and
boundary_geom are preserved. in_local_db is recomputed against plateau_buildings.

Usage:
  python3 load_city_master.py plateau_city_master_2025.csv --postgres-url "$DATABASE_URL"
"""
import argparse
import csv
import sys

import psycopg2
from psycopg2.extras import execute_values

COLS = ["city_code", "prefecture", "region", "city_name", "building_lods", "spec_versions"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = set(COLS) - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"CSV missing columns: {sorted(missing)} (found {reader.fieldnames})")
        rows = []
        for r in reader:
            code = (r["city_code"] or "").strip()
            if not code:
                continue
            rows.append((code, r["prefecture"].strip(), r["region"].strip(),
                         r["city_name"].strip(), r["building_lods"].strip(),
                         r["spec_versions"].strip()))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Load PLATEAU city master CSV into dash_city_master.")
    ap.add_argument("csv_path")
    ap.add_argument("--postgres-url", required=True)
    args = ap.parse_args()

    rows = read_csv(args.csv_path)
    if not rows:
        sys.exit("no rows in CSV")

    conn = psycopg2.connect(args.postgres_url)
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO dash_city_master
                  (city_code, prefecture, region, city_name, building_lods, spec_versions,
                   plateau_provided, updated_at)
                VALUES %s
                ON CONFLICT (city_code) DO UPDATE SET
                  prefecture    = EXCLUDED.prefecture,
                  region        = EXCLUDED.region,
                  city_name     = EXCLUDED.city_name,
                  building_lods = EXCLUDED.building_lods,
                  spec_versions = EXCLUDED.spec_versions,
                  plateau_provided = TRUE,
                  updated_at    = now()
            """, [(c, pref, reg, name, lods, spec, True) for (c, pref, reg, name, lods, spec) in rows],
                template="(%s,%s,%s,%s,%s,%s,%s, now())")

            # Recompute in_local_db against plateau_buildings (indexed on city_code).
            cur.execute("UPDATE dash_city_master SET in_local_db = FALSE;")
            cur.execute("""
                UPDATE dash_city_master m SET in_local_db = TRUE
                WHERE EXISTS (SELECT 1 FROM plateau_buildings p WHERE p.city_code = m.city_code);
            """)

            cur.execute("SELECT count(*) FILTER (WHERE plateau_provided), "
                        "count(*) FILTER (WHERE in_local_db) FROM dash_city_master;")
            provided, in_db = cur.fetchone()
        print(f"upserted {len(rows)} rows; master total provided={provided}, in_local_db={in_db}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
