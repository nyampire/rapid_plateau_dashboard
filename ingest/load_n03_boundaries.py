#!/usr/bin/env python3
"""Load N03 administrative boundaries into dash_city_master.boundary_geom.

Source: 国土数値情報 行政区域データ (N03), CC BY 4.0, 国土交通省.
  https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html
Download the national zip (e.g. N03-20250101_GML.zip), unzip, and pass the
.geojson (or .shp / .gml). N03 ships as JGD2011; we reproject to EPSG:4326.

N03 attributes used (others ignored):
  N03_001 prefecture name        e.g. 神奈川県
  N03_004 municipality name       e.g. 横浜市   (parent city for 政令市 wards)
  N03_005 designated-city ward    e.g. 鶴見区   (non-empty ONLY for 政令市 wards)
  N03_007 5-digit admin code      e.g. 14101

city_code assignment:
  - normal municipality / 東京特別区 (N03_005 empty): N03_007 IS the city_code.
  - 政令市 ward (N03_005 non-empty): N03_007 is a ward code (横浜市 鶴見区 = 14101),
    but PLATEAU keys 政令市 by the parent code (横浜市 = 14100). We map the ward to
    its parent by joining (N03_001, N03_004) to dash_city_master(prefecture, city_name),
    then dissolve every ward into the parent.

Only codes present in dash_city_master receive a boundary; everything else (所属未定地,
lakes, municipalities PLATEAU doesn't cover) is dropped. Each city's polygons (islands,
exclaves, dissolved wards) are merged with ST_UnaryUnion. Idempotent: re-running
replaces boundary_geom for whichever cities the source contains.

The national N03 is huge (~530 MB GeoJSON). Pre-simplify before shipping to a small
host so the union stays light, e.g.:
  ogr2ogr -simplify 0.0001 -t_srs EPSG:4326 -nlt MULTIPOLYGON \
    -select N03_001,N03_004,N03_005,N03_007 N03_small.geojson N03-20250101.geojson
Then run this with --simplify 0 (input already thinned). On the full file, pass a
--simplify tolerance to thin during load instead.

Usage:
  python3 load_n03_boundaries.py N03_small.geojson --postgres-url "$DATABASE_URL"
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.parse

import psycopg2

STAGING = "dash_n03_staging"


def ogr_pg(url):
    """Build ogr2ogr's 'PG:' connection string and a subprocess env.

    The password goes through PGPASSWORD, not the 'PG:' string, so it never lands
    in argv (visible to other users via ps/proc). Mirrors load_osm_buildings.py.
    """
    u = urllib.parse.urlparse(url)
    parts = [f"host={u.hostname or 'localhost'}", f"port={u.port or 5432}",
             f"dbname={(u.path or '/').lstrip('/')}"]
    if u.username:
        parts.append(f"user={u.username}")
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    return "PG:" + " ".join(parts), env


# city_code per N03 polygon, then dissolve to one MultiPolygon per city.
# A ward (N03_005 set) takes its parent's code from the master name join; otherwise
# N03_007 is used directly. ST_UnaryUnion merges a city's many polygons / wards.
#
# Cleanup CTEs (parts -> cleaned -> recombined) drop sub-threshold disconnected
# polygons and interior rings. When the input is per-feature-simplified (the
# normal pre-shipping case), adjacent ward borders simplify independently and
# the union leaves 5-30 m slivers along the seams — both as separate tiny
# MultiPolygon components and as tiny interior rings. The defaults remove those
# while preserving real features (broad areas, port islands, and known enclaves
# like 広島市 ⊃ 府中町 ≈ 10.4 km²).
DISSOLVE_SQL = f"""
WITH mapped AS (
  SELECT CASE WHEN NULLIF(TRIM(s.n03_005), '') IS NOT NULL THEN m.city_code
              ELSE s.n03_007 END        AS city_code,
         ST_MakeValid(s.geom)           AS geom
  FROM {STAGING} s
  LEFT JOIN dash_city_master m
    ON NULLIF(TRIM(s.n03_005), '') IS NOT NULL
   AND m.prefecture = s.n03_001
   AND m.city_name  = s.n03_004
),
dissolved AS (
  SELECT city_code,
         ST_CollectionExtract(ST_UnaryUnion(ST_Collect(geom)), 3) AS geom
  FROM mapped
  WHERE city_code IN (SELECT city_code FROM dash_city_master)
  GROUP BY city_code
),
parts AS (
  SELECT d.city_code, p.geom AS poly
  FROM dissolved d, LATERAL ST_Dump(d.geom) p
  WHERE GeometryType(p.geom) = 'POLYGON'
    AND ST_Area(p.geom::geography) >= %(part_thr)s
),
cleaned AS (
  SELECT pa.city_code,
    ST_MakePolygon(
      ST_ExteriorRing(pa.poly),
      COALESCE((SELECT array_agg(ST_ExteriorRing(r.geom)) FROM ST_DumpRings(pa.poly) r
                WHERE r.path[1] > 0 AND ST_Area(r.geom::geography) >= %(hole_thr)s),
               ARRAY[]::geometry[])
    ) AS poly FROM parts pa
),
recombined AS (
  SELECT city_code, ST_Multi(ST_Collect(poly)) AS geom FROM cleaned GROUP BY city_code
)
UPDATE dash_city_master t
SET boundary_geom = ST_Multi(
      CASE WHEN %(tol)s > 0
           THEN ST_SimplifyPreserveTopology(r.geom, %(tol)s)
           ELSE r.geom END),
    updated_at = now()
FROM recombined r
WHERE r.city_code = t.city_code;
"""

# 政令市 wards whose (prefecture, city_name) didn't match any master row.
UNMATCHED_WARDS_SQL = f"""
SELECT DISTINCT s.n03_001, s.n03_004
FROM {STAGING} s
WHERE NULLIF(TRIM(s.n03_005), '') IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dash_city_master m
                  WHERE m.prefecture = s.n03_001 AND m.city_name = s.n03_004)
ORDER BY 1, 2;
"""


def main():
    ap = argparse.ArgumentParser(description="Load N03 admin boundaries into dash_city_master.boundary_geom.")
    ap.add_argument("n03_source", help="N03 OGR source (.geojson / .shp / .gml)")
    ap.add_argument("--postgres-url", required=True)
    ap.add_argument("--simplify", type=float, default=0.0,
                    help="ogr2ogr -simplify tolerance (deg) applied during load; 0 = none "
                         "(use when the input is already thinned). Default 0.")
    ap.add_argument("--store-simplify", type=float, default=0.0,
                    help="ST_SimplifyPreserveTopology tolerance (deg) applied to the dissolved "
                         "boundary before storing; 0 = store as-is. Default 0.")
    ap.add_argument("--min-part-m2", type=float, default=1000.0,
                    help="Drop disconnected MultiPolygon parts smaller than this (m^2). Removes "
                         "sliver artifacts from per-feature simplification of ward boundaries; "
                         "1000 m^2 (~32 m square) is well below any real islet. Default 1000.")
    ap.add_argument("--min-hole-m2", type=float, default=10000.0,
                    help="Drop interior rings (holes) smaller than this (m^2). Keeps real "
                         "enclaves like 広島市 ⊃ 府中町 (10.4 km^2). Default 10000 (0.01 km^2).")
    args = ap.parse_args()

    pg_conn, pg_env = ogr_pg(args.postgres_url)
    cmd = ["ogr2ogr", "-f", "PostgreSQL", pg_conn, args.n03_source,
           "-nln", STAGING, "-overwrite", "-t_srs", "EPSG:4326",
           "-nlt", "MULTIPOLYGON", "-lco", "GEOMETRY_NAME=geom",
           "-select", "N03_001,N03_004,N03_005,N03_007"]
    if args.simplify > 0:
        cmd += ["-simplify", str(args.simplify)]
    print(f"ogr2ogr -> staging {STAGING} (4 fields, EPSG:4326"
          f"{', simplify ' + str(args.simplify) if args.simplify > 0 else ''}) ...")
    t0 = time.time()
    subprocess.run(cmd, check=True, env=pg_env)
    print(f"[time] ogr2ogr staging: {time.time() - t0:.1f}s")

    conn = psycopg2.connect(args.postgres_url)
    conn.autocommit = False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {STAGING};")
            print(f"staging rows: {cur.fetchone()[0]}")

            t1 = time.time()
            cur.execute(DISSOLVE_SQL, {"tol": args.store_simplify,
                                       "part_thr": args.min_part_m2,
                                       "hole_thr": args.min_hole_m2})
            updated = cur.rowcount
            print(f"[time] dissolve+cleanup+update: {time.time() - t1:.1f}s "
                  f"({updated} cities, parts<{args.min_part_m2:g}m^2 and "
                  f"holes<{args.min_hole_m2:g}m^2 dropped)")

            cur.execute(UNMATCHED_WARDS_SQL)
            unmatched = cur.fetchall()
            if unmatched:
                print(f"WARNING: {len(unmatched)} 政令市 ward group(s) unmatched in master:")
                for pref, name in unmatched:
                    print(f"  - {pref} {name}")

            cur.execute("SELECT count(*) FILTER (WHERE boundary_geom IS NOT NULL), count(*) "
                        "FROM dash_city_master;")
            with_geom, total = cur.fetchone()
            cur.execute("SELECT city_code, city_name FROM dash_city_master "
                        "WHERE boundary_geom IS NULL ORDER BY city_code;")
            missing = cur.fetchall()
            cur.execute(f"DROP TABLE IF EXISTS {STAGING};")
        print(f"boundary_geom set for {with_geom}/{total} master cities")
        if missing:
            print(f"{len(missing)} master cities still without a boundary:")
            for code, name in missing:
                print(f"  - {code} {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
