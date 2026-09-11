#!/usr/bin/env python3
"""Load OSM building geometries (GeoJSONSeq from osmium export) into dash_osm_buildings.

Input is produced by the low-memory pipeline (DESIGN.md §3.1):
  osmium export region.osm.pbf --add-unique-id=type_id --index-type=sparse_file_array \
    --geometry-types=polygon -f geojsonseq -o - | grep '"building":' > buildings.geojsonseq

Each feature id is osmium's area form 'a<num>' (num even => way, odd => relation).
city_code is assigned by which plateau_coverage polygon contains the building's
representative point (interim until N03 admin boundaries; DESIGN.md §9-1). Buildings
outside every coverage polygon are dropped.
Idempotent: a reload replaces the rows this region's extract produced last time,
identified by source_region.
Region extracts overlap at the border, so a building that appears in two regions
is kept as one row by the (osm_type, osm_id) unique index.

Usage:
  python3 load_osm_buildings.py buildings.geojsonseq --postgres-url "$DATABASE_URL" --region kanto
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.parse

import psycopg2

STAGING = "dash_load_tmp"

# Geofabrik の地域名の一覧。
# --region に渡した値はそのまま source_region 列に入る。
# 次回の読み込みでは、この値で source_region の照合をして古い行を削除する。
# 綴りを誤ると、削除が 1 件も当たらないまま誤った名前で挿入されてしまう。
REGIONS = ["hokkaido", "tohoku", "kanto", "chubu",
           "kansai", "chugoku", "shikoku", "kyushu"]


def ogr_pg(url):
    """Build ogr2ogr's 'PG:' connection string and an env dict for the subprocess.

    The password is passed via PGPASSWORD in the environment, NOT in the 'PG:'
    string, so it never appears in argv (visible to other users via `ps`/proc).
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


def decode_select_sql(staging):
    """SELECT that decodes osmium 'a<num>' ids to (osm_type, osm_id) and assigns a
    city_code by which admin polygon contains the building's representative point.

    The polygon is the N03 administrative boundary (dash_city_master.boundary_geom,
    loaded by load_n03_boundaries.py), falling back to the plateau_coverage hull for
    cities/areas without an N03 boundary (special datasets like 竹芝/万博, or cities
    absent from N03). Both probes use GiST indexes; buildings in neither are dropped.

    Only POLYGON/MULTIPOLYGON rows are kept. ST_MakeValid runs in the outer SELECT so
    it only touches rows that survive the spatial join — the (often majority)
    buildings outside every admin polygon skip that validity work."""
    return f"""
        SELECT loc.city_code,
               CASE WHEN s.n % 2 = 0 THEN 'w' ELSE 'r' END AS osm_type,
               CASE WHEN s.n % 2 = 0 THEN s.n / 2 ELSE (s.n - 1) / 2 END AS osm_id,
               ST_MakeValid(ST_Multi(s.geom)) AS geom
        FROM (
          SELECT (substring(id from 2))::bigint AS n,
                 geom,
                 ST_PointOnSurface(geom) AS pt
          FROM {staging}
          WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
            AND id ~ '^[a-z][0-9]+$'
        ) s
        JOIN LATERAL (
          SELECT city_code FROM (
            SELECT m.city_code, 1 AS pri FROM dash_city_master m
              WHERE m.boundary_geom IS NOT NULL AND ST_Contains(m.boundary_geom, s.pt)
            UNION ALL
            SELECT cov.city_code, 2 AS pri FROM plateau_coverage cov
              WHERE ST_Contains(cov.geom, s.pt)
          ) cand ORDER BY pri LIMIT 1
        ) loc ON true
    """


def replace_region_rows(cur, region, decoded="_decoded"):
    """この地域の抽出から来た行を、新しい抽出の内容で入れ替える。

    削除の範囲は「この地域から来た行」であって、「入力に現れた市区町村」ではない。
    地域の抽出は境界の外へ少しはみ出す。
    市区町村で削除すると、隣の地域が入れたその市の行まで消えてしまう。
    2026-09-11 には、これでいわき市の 14 万件が 386 件になっていた。

    県境の建物は隣り合う 2 つの抽出に現れる。
    一意索引で 1 行にまとめ、所属を後から読んだ地域に移す。
    翌週にその地域が消して入れ直すため、行は毎週更新される。

    削除した行数を返す。
    """
    cur.execute("DELETE FROM dash_osm_buildings WHERE source_region = %s;", (region,))
    deleted = cur.rowcount
    cur.execute(
        "INSERT INTO dash_osm_buildings (city_code, osm_type, osm_id, geom, source_region) "
        f"SELECT city_code, osm_type, osm_id, geom, %s FROM {decoded} "
        "ON CONFLICT (osm_type, osm_id) DO UPDATE SET "
        "  city_code = EXCLUDED.city_code, "
        "  geom = EXCLUDED.geom, "
        "  source_region = EXCLUDED.source_region, "
        "  fetched_at = now();",
        (region,))
    return deleted


def main():
    ap = argparse.ArgumentParser(description="Load OSM buildings GeoJSONSeq into dash_osm_buildings.")
    ap.add_argument("geojsonseq")
    ap.add_argument("--postgres-url", required=True)
    ap.add_argument("--region", required=True, choices=REGIONS,
                    help="Geofabrik の地域名（hokkaido / tohoku / kanto / chubu / "
                         "kansai / chugoku / shikoku / kyushu）。"
                         "この名前で入れ替える範囲が決まる。")
    args = ap.parse_args()

    print(f"ogr2ogr -> staging {STAGING} (id + geometry only) ...")
    t0 = time.time()
    pg_conn, pg_env = ogr_pg(args.postgres_url)
    # -select id: keep only the feature id; drop the hundreds of OSM tag columns we never use
    # (the big I/O win for multi-million-row regions).
    subprocess.run(["ogr2ogr", "-f", "PostgreSQL", pg_conn, args.geojsonseq,
                    "-nln", STAGING, "-overwrite", "-lco", "GEOMETRY_NAME=geom",
                    "-nlt", "PROMOTE_TO_MULTI", "-select", "id"], check=True, env=pg_env)
    print(f"[time] ogr2ogr staging: {time.time() - t0:.1f}s")

    conn = psycopg2.connect(args.postgres_url)
    conn.autocommit = False
    try:
        with conn, conn.cursor() as cur:
            # Decode osm type/id and assign one city via coverage (overlapping hulls -> pick one).
            t1 = time.time()
            cur.execute("CREATE TEMP TABLE _decoded ON COMMIT DROP AS "
                        + decode_select_sql(STAGING) + ";")
            cur.execute("SELECT count(*), count(DISTINCT city_code) FROM _decoded;")
            n_rows, n_cities = cur.fetchone()
            print(f"[time] coverage-join/decode: {time.time() - t1:.1f}s ({n_rows} rows)")
            if n_rows == 0:
                print("no OSM buildings fell within any coverage polygon; nothing to load")
                cur.execute(f"DROP TABLE IF EXISTS {STAGING};")
                return

            t2 = time.time()
            deleted = replace_region_rows(cur, args.region)
            cur.execute(f"DROP TABLE IF EXISTS {STAGING};")
            print(f"[time] delete+insert: {time.time() - t2:.1f}s")
        print(f"loaded {n_rows} OSM buildings across {n_cities} cities "
              f"as region {args.region} (replaced {deleted} rows of that region)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
