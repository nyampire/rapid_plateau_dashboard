#!/usr/bin/env python3
"""Compute per-city import-rate stats and the overall progress rollup.

Reads plateau_buildings (read-only) and dash_osm_buildings; writes dash_city_stats
and appends to dash_progress_history.

Import rate (DESIGN.md §2.1): a PLATEAU outline (building_part IS NULL) counts as
"present in OSM" if its representative point (ST_PointOnSurface) is inside some OSM
building, OR the area overlap exceeds 30%. Denominator = outlines only.
The OSM match is purely spatial (no o.city_code filter): an OSM building assigned to a
neighbouring city by the coverage-hull join still counts, avoiding border under-counting.

Optimization: the cheap point-in-polygon EXISTS is evaluated first; the expensive
ST_Intersection area test only runs (OR short-circuit) when the point test fails.

Usage:
  python3 compute_stats.py --postgres-url "$DATABASE_URL" [--city 11230] [--skip-history]
"""
import argparse
import sys
import time

import psycopg2

LOCK_KEY = "dash_compute_stats"

STATS_SQL = """
WITH p AS (
  SELECT geom, ST_PointOnSurface(geom) AS pt
  FROM plateau_buildings
  WHERE city_code = %(city)s AND building_part IS NULL
)
SELECT
  count(*) AS plateau_count,
  count(*) FILTER (WHERE
    EXISTS (SELECT 1 FROM dash_osm_buildings o
            WHERE o.geom && p.geom
              AND ST_Contains(o.geom, p.pt))
    OR EXISTS (SELECT 1 FROM dash_osm_buildings o
               WHERE o.geom && p.geom
                 AND ST_Area(ST_Intersection(ST_MakeValid(p.geom), ST_MakeValid(o.geom))) / NULLIF(ST_Area(p.geom), 0) > 0.30)
  ) AS intersecting
FROM p;
"""


def target_cities(cur, city):
    if city:
        return [city]
    cur.execute("""
        SELECT DISTINCT d.city_code
        FROM dash_osm_buildings d
        JOIN dash_city_master m ON m.city_code = d.city_code AND m.in_local_db
        ORDER BY 1;
    """)
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(description="Compute dash_city_stats + dash_progress_history.")
    ap.add_argument("--postgres-url", required=True)
    ap.add_argument("--city", help="restrict to one city_code (for testing)")
    ap.add_argument("--skip-history", action="store_true")
    args = ap.parse_args()

    conn = psycopg2.connect(args.postgres_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s));", (LOCK_KEY,))
            if not cur.fetchone()[0]:
                sys.exit("another compute_stats run holds the advisory lock; aborting")

            cities = target_cities(cur, args.city)
            print(f"computing stats for {len(cities)} city/cities")

            skipped = []
            for c in cities:
                t0 = time.time()
                try:
                    cur.execute(STATS_SQL, {"city": c})
                    plateau, inter = cur.fetchone()
                    cur.execute("SELECT count(*) FROM dash_osm_buildings WHERE city_code=%s;", (c,))
                    osm = cur.fetchone()[0]
                    rate = round(100.0 * inter / plateau, 2) if plateau else None
                    cur.execute("""
                        INSERT INTO dash_city_stats
                          (city_code, plateau_count, osm_count, intersecting_count, import_rate, computed_at)
                        VALUES (%s,%s,%s,%s,%s, now())
                        ON CONFLICT (city_code) DO UPDATE SET
                          plateau_count=EXCLUDED.plateau_count, osm_count=EXCLUDED.osm_count,
                          intersecting_count=EXCLUDED.intersecting_count, import_rate=EXCLUDED.import_rate,
                          computed_at=EXCLUDED.computed_at;
                    """, (c, plateau, osm, inter, rate))
                    conn.commit()
                    print(f"  {c}: plateau={plateau} osm={osm} intersecting={inter} "
                          f"rate={rate}% ({time.time()-t0:.1f}s)")
                except Exception as e:
                    conn.rollback()  # keep the session (and advisory lock) usable
                    skipped.append(c)
                    print(f"  SKIP {c}: {type(e).__name__}: {str(e).splitlines()[0]}")
            if skipped:
                print(f"skipped {len(skipped)} city/cities: {','.join(skipped)}")

            if not args.skip_history:
                cur.execute("""
                    INSERT INTO dash_progress_history
                      (computed_at, total_plateau, total_intersecting, overall_rate,
                       cities_total, cities_in_db, cities_osm_done)
                    SELECT now(),
                      COALESCE(sum(s.plateau_count),0),
                      COALESCE(sum(s.intersecting_count),0),
                      round(100.0*COALESCE(sum(s.intersecting_count),0)
                            / NULLIF(sum(s.plateau_count),0), 2),
                      (SELECT count(*) FROM dash_city_master),
                      (SELECT count(*) FROM dash_city_master WHERE in_local_db),
                      (SELECT count(*) FROM dash_city_master WHERE osm_import_status='done')
                    FROM dash_city_stats s;
                """)
                conn.commit()
                cur.execute("""SELECT total_plateau, total_intersecting, overall_rate,
                               cities_total, cities_in_db, cities_osm_done
                               FROM dash_progress_history ORDER BY computed_at DESC LIMIT 1;""")
                tp, ti, orr, ct, cdb, cod = cur.fetchone()
                print(f"progress: overall_rate={orr}% ({ti}/{tp}); "
                      f"cities total={ct} in_db={cdb} osm_done={cod}")

            cur.execute("SELECT pg_advisory_unlock(hashtext(%s));", (LOCK_KEY,))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
