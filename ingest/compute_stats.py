#!/usr/bin/env python3
"""Compute per-city import-rate stats and the overall progress rollup.

Reads plateau_buildings (read-only) and dash_osm_buildings; writes dash_city_stats
and appends to dash_progress_history.

Import rate (DESIGN.md §2.1): a PLATEAU outline (building_part IS NULL) counts as
"present in OSM" if its representative point (ST_PointOnSurface) is inside some OSM
building, OR the area overlap exceeds 30%. Denominator = outlines only.
The OSM match is purely spatial (no o.city_code filter): an OSM building assigned to a
neighbouring city by the coverage-hull join still counts, avoiding border under-counting.

Optimizations:
- The cheap point-in-polygon EXISTS is evaluated first; the expensive
  ST_Intersection area test only runs (OR short-circuit) when the point test fails.
- Each city is one PostgreSQL statement; the per-city work is independent so we
  run them across a multiprocessing.Pool (--workers, default 4). Big cities are
  scheduled first (longest-processing-time-first) using last week's plateau_count
  so straggler tails don't dominate wall time.

Usage:
  python3 compute_stats.py --postgres-url "$DATABASE_URL" [--city 11230] [--skip-history]
  python3 compute_stats.py --postgres-url "$DATABASE_URL" --workers 1   # serial fallback
"""
import argparse
import multiprocessing as mp
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

# Per-ward variant. PLATEAU outlines are spatially filtered by the ward boundary
# (parent city's buildings are split across its wards). The OR-short-circuit
# OSM intersection test is identical and stays purely spatial: an OSM building
# assigned to a neighbouring city by coverage still counts, matching the
# city-level behaviour so ward sums roll up cleanly to the parent's row.
WARD_STATS_SQL = """
WITH w AS (
  SELECT parent_city_code AS pcc, boundary_geom AS wgeom
  FROM dash_ward_master WHERE ward_code = %(ward)s
),
p AS (
  SELECT pb.geom, ST_PointOnSurface(pb.geom) AS pt
  FROM plateau_buildings pb, w
  WHERE pb.city_code = w.pcc
    AND pb.building_part IS NULL
    AND pb.geom && w.wgeom
    AND ST_Contains(w.wgeom, ST_PointOnSurface(pb.geom))
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

# Ward OSM count: OSM buildings whose representative point falls inside the ward
# (and whose coverage-assigned city_code is the parent). Mirrors the city-level
# count semantics (parent's OSM total partitioned across its wards).
WARD_OSM_COUNT_SQL = """
SELECT count(*)
FROM dash_osm_buildings o, dash_ward_master w
WHERE w.ward_code = %(ward)s
  AND o.city_code = w.parent_city_code
  AND o.geom && w.boundary_geom
  AND ST_Contains(w.boundary_geom, ST_PointOnSurface(o.geom));
"""

# Issue #14: each snapshot writes one row per region plus one '__overall__'
# grand-total row. GROUPING SETS gives us both in a single INSERT; GROUPING()
# flags the grand total so a city with NULL region (shouldn't happen, but
# defensive) doesn't collide with the sentinel.
PROGRESS_HISTORY_INSERT_SQL = """
INSERT INTO dash_progress_history
  (computed_at, region, total_plateau, total_intersecting, overall_rate,
   cities_total, cities_in_db, cities_osm_done)
WITH cb AS (
  SELECT m.region, m.in_local_db, m.osm_import_status,
         s.plateau_count, s.intersecting_count
  FROM dash_city_master m
  LEFT JOIN dash_city_stats s ON s.city_code = m.city_code
)
SELECT
  now(),
  CASE WHEN GROUPING(cb.region) = 1 THEN '__overall__'
       ELSE COALESCE(cb.region, '__unknown__') END,
  COALESCE(sum(cb.plateau_count), 0),
  COALESCE(sum(cb.intersecting_count), 0),
  round(100.0 * COALESCE(sum(cb.intersecting_count), 0)
        / NULLIF(sum(cb.plateau_count), 0), 2),
  count(*),
  count(*) FILTER (WHERE cb.in_local_db),
  count(*) FILTER (WHERE cb.osm_import_status = 'done')
FROM cb
GROUP BY GROUPING SETS ((cb.region), ());
"""


def target_cities(cur, city):
    if city:
        return [city]
    # Schedule biggest cities first (LPT). Previous week's plateau_count is the
    # best estimator we have; cities without prior stats sort last (NULLS LAST)
    # at a small alphabetical cost.
    cur.execute("""
        SELECT d.city_code
        FROM (SELECT DISTINCT city_code FROM dash_osm_buildings) d
        JOIN dash_city_master m ON m.city_code = d.city_code AND m.in_local_db
        LEFT JOIN dash_city_stats s ON s.city_code = d.city_code
        ORDER BY s.plateau_count DESC NULLS LAST, d.city_code;
    """)
    return [r[0] for r in cur.fetchall()]


def target_wards(cur, ward):
    if ward:
        return [ward]
    # Same LPT scheduling as cities.
    cur.execute("""
        SELECT w.ward_code
        FROM dash_ward_master w
        JOIN dash_city_master m ON m.city_code = w.parent_city_code AND m.in_local_db
        LEFT JOIN dash_ward_stats s ON s.ward_code = w.ward_code
        ORDER BY s.plateau_count DESC NULLS LAST, w.ward_code;
    """)
    return [r[0] for r in cur.fetchall()]


# --- Worker process plumbing for the city / ward parallel pools ------------
# Each worker holds its own psycopg2 connection so commits don't contend with
# the main process's advisory-lock session. autocommit so each city's UPSERT
# stands alone — a single city's GEOS exception only loses that one row.

_worker_conn = None  # process-local, set by _worker_init


def _worker_init(pg_url):
    global _worker_conn
    _worker_conn = psycopg2.connect(pg_url)
    _worker_conn.autocommit = True


def _compute_one_city(city_code):
    t0 = time.time()
    try:
        with _worker_conn.cursor() as cur:
            cur.execute(STATS_SQL, {"city": city_code})
            plateau, inter = cur.fetchone()
            cur.execute("SELECT count(*) FROM dash_osm_buildings WHERE city_code=%s;", (city_code,))
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
            """, (city_code, plateau, osm, inter, rate))
        return (city_code, plateau, osm, inter, rate, time.time() - t0, None)
    except Exception as e:
        return (city_code, None, None, None, None, time.time() - t0,
                f"{type(e).__name__}: {str(e).splitlines()[0]}")


def _compute_one_ward(ward_code):
    t0 = time.time()
    try:
        with _worker_conn.cursor() as cur:
            cur.execute(WARD_STATS_SQL, {"ward": ward_code})
            plateau, inter = cur.fetchone()
            cur.execute(WARD_OSM_COUNT_SQL, {"ward": ward_code})
            osm = cur.fetchone()[0]
            rate = round(100.0 * inter / plateau, 2) if plateau else None
            cur.execute("""
                INSERT INTO dash_ward_stats
                  (ward_code, plateau_count, osm_count, intersecting_count, import_rate, computed_at)
                VALUES (%s,%s,%s,%s,%s, now())
                ON CONFLICT (ward_code) DO UPDATE SET
                  plateau_count=EXCLUDED.plateau_count, osm_count=EXCLUDED.osm_count,
                  intersecting_count=EXCLUDED.intersecting_count, import_rate=EXCLUDED.import_rate,
                  computed_at=EXCLUDED.computed_at;
            """, (ward_code, plateau, osm, inter, rate))
        return (ward_code, plateau, osm, inter, rate, time.time() - t0, None)
    except Exception as e:
        return (ward_code, None, None, None, None, time.time() - t0,
                f"{type(e).__name__}: {str(e).splitlines()[0]}")


def _run_pool(fn, items, workers, pg_url):
    """Run `fn(item)` over `items`, yielding results as they finish.

    Single-process fallback when workers == 1 (also useful in tests where
    forking with an open connection can be surprising). Otherwise uses
    multiprocessing.Pool.imap_unordered so big-city stragglers don't block
    the progress stream.
    """
    if workers <= 1:
        _worker_init(pg_url)
        try:
            for it in items:
                yield fn(it)
        finally:
            global _worker_conn
            if _worker_conn:
                _worker_conn.close()
                _worker_conn = None
    else:
        with mp.Pool(processes=workers, initializer=_worker_init, initargs=(pg_url,)) as pool:
            yield from pool.imap_unordered(fn, items)


def main():
    ap = argparse.ArgumentParser(description="Compute dash_city_stats + dash_progress_history.")
    ap.add_argument("--postgres-url", required=True)
    ap.add_argument("--city", help="restrict to one city_code (for testing)")
    ap.add_argument("--ward", help="restrict to one ward_code (for testing)")
    ap.add_argument("--skip-history", action="store_true")
    ap.add_argument("--skip-cities", action="store_true",
                    help="skip dash_city_stats this run (for ward-only catchup)")
    ap.add_argument("--skip-wards", action="store_true",
                    help="don't compute dash_ward_stats this run (for emergency disable)")
    ap.add_argument("--workers", type=int, default=4,
                    help="number of parallel worker processes (1 = serial fallback). Default 4.")
    args = ap.parse_args()

    conn = psycopg2.connect(args.postgres_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s));", (LOCK_KEY,))
            if not cur.fetchone()[0]:
                sys.exit("another compute_stats run holds the advisory lock; aborting")

            cities = [] if args.skip_cities else target_cities(cur, args.city)
            if cities:
                print(f"computing stats for {len(cities)} city/cities ({args.workers} workers)")
            elif args.skip_cities:
                print("--skip-cities set: dash_city_stats unchanged this run")

            skipped = []
            if cities:
                for r in _run_pool(_compute_one_city, cities, args.workers, args.postgres_url):
                    code, plateau, osm, inter, rate, elapsed, err = r
                    if err:
                        skipped.append(code)
                        print(f"  SKIP {code}: {err}")
                    else:
                        print(f"  {code}: plateau={plateau} osm={osm} intersecting={inter} "
                              f"rate={rate}% ({elapsed:.1f}s)")
            if skipped:
                print(f"skipped {len(skipped)} city/cities: {','.join(skipped)}")

            if not args.skip_wards:
                wards = target_wards(cur, args.ward)
                if wards:
                    print(f"computing stats for {len(wards)} ward(s) ({args.workers} workers)")
                ward_skipped = []
                if wards:
                    for r in _run_pool(_compute_one_ward, wards, args.workers, args.postgres_url):
                        code, plateau, osm, inter, rate, elapsed, err = r
                        if err:
                            ward_skipped.append(code)
                            print(f"  SKIP ward {code}: {err}")
                        else:
                            print(f"  ward {code}: plateau={plateau} osm={osm} intersecting={inter} "
                                  f"rate={rate}% ({elapsed:.1f}s)")
                if ward_skipped:
                    print(f"skipped {len(ward_skipped)} ward(s): {','.join(ward_skipped)}")

            if not args.skip_history:
                cur.execute(PROGRESS_HISTORY_INSERT_SQL)
                conn.commit()
                cur.execute("""
                    SELECT region, total_plateau, total_intersecting, overall_rate,
                           cities_total, cities_in_db, cities_osm_done
                    FROM dash_progress_history
                    WHERE computed_at = (SELECT max(computed_at) FROM dash_progress_history)
                    ORDER BY region = '__overall__' DESC, region;
                """)
                rows = cur.fetchall()
                for region, tp, ti, orr, ct, cdb, cod in rows:
                    label = "全国" if region == '__overall__' else region
                    print(f"progress[{label}]: rate={orr}% ({ti}/{tp}); "
                          f"cities total={ct} in_db={cdb} osm_done={cod}")

            cur.execute("SELECT pg_advisory_unlock(hashtext(%s));", (LOCK_KEY,))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
