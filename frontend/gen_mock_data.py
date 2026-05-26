#!/usr/bin/env python3
"""Generate frontend/data.js for the dashboard mock from real dash_* data.

Writes `window.DASH = { summary, regions, cities, geojson }` so the mock works
from file:// or a static server (no fetch/CORS, no API yet).

Usage:
  python3 gen_mock_data.py --postgres-url "$DATABASE_URL" -o data.js
"""
import argparse
import json

import psycopg2

SUMMARY = """
SELECT json_build_object(
  'overall_rate', h.overall_rate,
  'total_plateau', h.total_plateau,
  'total_intersecting', h.total_intersecting,
  'cities_total', (SELECT count(*) FROM dash_city_master),
  'cities_in_db', (SELECT count(*) FROM dash_city_master WHERE in_local_db),
  'cities_osm_done', (SELECT count(*) FROM dash_city_master WHERE osm_import_status='done'),
  'cities_measured', (SELECT count(*) FROM dash_city_stats),
  'computed_at', to_char(h.computed_at, 'YYYY-MM-DD')
)
FROM dash_progress_history h ORDER BY h.computed_at DESC LIMIT 1;
"""

REGIONS = """
SELECT COALESCE(json_agg(r), '[]'::json) FROM (
  SELECT m.region,
    count(*) AS cities_total,
    count(*) FILTER (WHERE m.in_local_db) AS cities_in_db,
    count(*) FILTER (WHERE m.osm_import_status='done') AS cities_done,
    count(s.city_code) AS cities_measured,
    sum(s.plateau_count) AS plateau,
    sum(s.intersecting_count) AS intersecting,
    round(100.0*sum(s.intersecting_count)/NULLIF(sum(s.plateau_count),0), 1) AS rate
  FROM dash_city_master m
  LEFT JOIN dash_city_stats s ON s.city_code = m.city_code
  GROUP BY m.region
  ORDER BY MIN(m.city_code)
) r;
"""

CITIES = """
SELECT COALESCE(json_agg(c), '[]'::json) FROM (
  SELECT m.city_code, m.city_name, m.prefecture, m.region, m.building_lods, m.spec_versions,
    m.in_local_db, m.osm_import_status,
    to_char(m.osm_import_date,'YYYY-MM-DD') AS osm_import_date, m.osm_validated,
    s.plateau_count, s.osm_count, s.intersecting_count, s.import_rate
  FROM dash_city_master m
  LEFT JOIN dash_city_stats s ON s.city_code = m.city_code
  ORDER BY s.import_rate DESC NULLS LAST, m.in_local_db DESC, m.city_code
) c;
"""

GEOJSON = """
SELECT json_build_object('type','FeatureCollection','features', COALESCE(json_agg(f), '[]'::json))
FROM (
  SELECT json_build_object('type','Feature',
    'properties', json_build_object('city_code', cov.city_code, 'city_name', m.city_name,
                                    'import_rate', s.import_rate),
    'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(cov.geom, 0.001))::json) AS f
  FROM plateau_coverage cov
  LEFT JOIN dash_city_master m ON m.city_code = cov.city_code
  LEFT JOIN dash_city_stats s ON s.city_code = cov.city_code
) x;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--postgres-url", required=True)
    ap.add_argument("-o", "--out", default="data.js")
    args = ap.parse_args()

    conn = psycopg2.connect(args.postgres_url)
    with conn.cursor() as cur:
        cur.execute(SUMMARY); summary = cur.fetchone()[0]
        cur.execute(REGIONS); regions = cur.fetchone()[0]
        cur.execute(CITIES); cities = cur.fetchone()[0]
        cur.execute(GEOJSON); geojson = cur.fetchone()[0]
    conn.close()

    data = {"summary": summary, "regions": regions, "cities": cities, "geojson": geojson}
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("window.DASH = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";\n")
    print(f"wrote {args.out}: {len(cities)} cities, {len(regions)} regions, "
          f"{len(geojson['features'])} coverage polys")


if __name__ == "__main__":
    main()
