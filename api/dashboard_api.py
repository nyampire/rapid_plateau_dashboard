#!/usr/bin/env python3
"""Read-only API for the Rapid Plateau progress dashboard.

Serves /api/dashboard/* from the dash_* tables (and plateau_coverage). Returns
JSON assembled in-database (json_build_object / json_agg) so handlers are thin.

Exposes both an APIRouter (`router`) for mounting into an existing FastAPI app
and a standalone `app` for `uvicorn dashboard_api:app`.

Env:  DASH_DATABASE_URL  preferred (a read-only role; see sql/readonly_role.sql)
      DATABASE_URL       fallback connection string
Run:  DATABASE_URL=... uvicorn dashboard_api:app --port 8000

This API only ever reads. Every connection is put in a read-only session
(set_session(readonly=True)) so it cannot write even if pointed at a
write-capable role; using a dedicated read-only role via DASH_DATABASE_URL
adds defense in depth.
"""
import os

import psycopg2
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/dashboard")


def _db_url():
    # Prefer a dedicated read-only role; fall back to the shared DATABASE_URL.
    return os.environ.get("DASH_DATABASE_URL") or os.environ.get("DATABASE_URL")


def fetch_one_json(sql, params=None):
    """Run a query whose first column is a single JSON value; return it (or None)."""
    url = _db_url()
    if not url:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True, autocommit=True)  # serving path never writes
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


# SUMMARY filters history to region='__overall__'. After issue #14, dash_progress_history
# also contains per-region rows; the unfiltered ORDER BY computed_at DESC would otherwise
# pick whichever region row happened to share the latest timestamp.
SUMMARY_SQL = """
SELECT json_build_object(
  'overall_rate',       (SELECT overall_rate FROM dash_progress_history WHERE region='__overall__' ORDER BY computed_at DESC LIMIT 1),
  'prev_rate',          (SELECT overall_rate FROM dash_progress_history WHERE region='__overall__' ORDER BY computed_at DESC OFFSET 1 LIMIT 1),
  'total_plateau',      (SELECT total_plateau FROM dash_progress_history WHERE region='__overall__' ORDER BY computed_at DESC LIMIT 1),
  'total_intersecting', (SELECT total_intersecting FROM dash_progress_history WHERE region='__overall__' ORDER BY computed_at DESC LIMIT 1),
  'cities_total',       (SELECT count(*) FROM dash_city_master),
  'cities_in_db',       (SELECT count(*) FROM dash_city_master WHERE in_local_db),
  'cities_osm_done',    (SELECT count(*) FROM dash_city_master WHERE osm_import_status='done'),
  'cities_measured',    (SELECT count(*) FROM dash_city_stats),
  'computed_at',        (SELECT to_char(computed_at,'YYYY-MM-DD') FROM dash_progress_history WHERE region='__overall__' ORDER BY computed_at DESC LIMIT 1),
  'trend',              (SELECT json_agg(json_build_object('date', to_char(computed_at,'YYYY-MM-DD'), 'rate', overall_rate))
                         FROM (SELECT computed_at, overall_rate FROM dash_progress_history WHERE region='__overall__' ORDER BY computed_at) s)
);
"""

# Per-region time series. Default region='__overall__' returns the same trend as
# /summary; pass region=関東 / 中部 / ... for a region-specific curve. Issue #14.
PROGRESS_SQL = """
SELECT json_build_object(
  'region',             %(region)s,
  'current_rate',       (SELECT overall_rate FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'prev_rate',          (SELECT overall_rate FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC OFFSET 1 LIMIT 1),
  'total_plateau',      (SELECT total_plateau FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'total_intersecting', (SELECT total_intersecting FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'cities_total',       (SELECT cities_total FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'cities_in_db',       (SELECT cities_in_db FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'cities_osm_done',    (SELECT cities_osm_done FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'computed_at',        (SELECT to_char(computed_at,'YYYY-MM-DD') FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at DESC LIMIT 1),
  'trend',              (SELECT json_agg(json_build_object('date', to_char(computed_at,'YYYY-MM-DD'), 'rate', overall_rate))
                         FROM (SELECT computed_at, overall_rate FROM dash_progress_history WHERE region=%(region)s ORDER BY computed_at) s)
);
"""

REGIONS_SQL = """
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

# city fields shared by /cities and /cities/{code}
# repr_lat/lon: ST_PointOnSurface of boundary_geom (or coverage fallback for cities
# absent from N03). Rounded to 6 dp (~0.1 m) — plenty for drawer deep-link URLs.
# null when neither N03 nor coverage is present; frontend then falls back to a
# name-search URL.
CITY_COLS = """
  m.city_code, m.city_name, m.prefecture, m.region, m.building_lods, m.spec_versions,
  m.in_local_db, m.osm_import_status,
  to_char(m.osm_import_date,'YYYY-MM-DD') AS osm_import_date, m.osm_validated,
  round(ST_Y(m.repr_point)::numeric, 6) AS repr_lat,
  round(ST_X(m.repr_point)::numeric, 6) AS repr_lon,
  s.plateau_count, s.osm_count, s.intersecting_count, s.import_rate
"""

CITIES_SQL = f"""
SELECT COALESCE(json_agg(c), '[]'::json) FROM (
  SELECT {CITY_COLS}
  FROM dash_city_master m
  LEFT JOIN dash_city_stats s ON s.city_code = m.city_code
  WHERE (%(region)s IS NULL OR m.region = %(region)s)
  ORDER BY m.city_code
) c;
"""

CITY_ONE_SQL = f"""
SELECT to_jsonb(c) FROM (
  SELECT {CITY_COLS}
  FROM dash_city_master m
  LEFT JOIN dash_city_stats s ON s.city_code = m.city_code
  WHERE m.city_code = %(code)s
) c;
"""

WARDS_SQL = """
SELECT COALESCE(json_agg(w ORDER BY w.ward_code), '[]'::json) FROM (
  SELECT w.ward_code, w.parent_city_code, w.ward_name,
    round(ST_Y(w.repr_point)::numeric, 6) AS repr_lat,
    round(ST_X(w.repr_point)::numeric, 6) AS repr_lon,
    s.plateau_count, s.osm_count, s.intersecting_count, s.import_rate
  FROM dash_ward_master w
  LEFT JOIN dash_ward_stats s ON s.ward_code = w.ward_code
) w;
"""

GEOJSON_SQL = """
SELECT json_build_object('type','FeatureCollection','features', COALESCE(json_agg(f), '[]'::json))
FROM (
  SELECT json_build_object('type','Feature',
    'properties', json_build_object('city_code', cov.city_code, 'city_name', m.city_name,
                                    'import_rate', s.import_rate),
    'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(
                  COALESCE(m.boundary_geom, cov.geom), 0.001))::json) AS f
  FROM plateau_coverage cov
  LEFT JOIN dash_city_master m ON m.city_code = cov.city_code
  LEFT JOIN dash_city_stats s ON s.city_code = cov.city_code
) x;
"""


@router.get("/summary")
def summary():
    return fetch_one_json(SUMMARY_SQL)


@router.get("/regions")
def regions():
    return fetch_one_json(REGIONS_SQL)


@router.get("/progress")
def progress(region: str = "__overall__"):
    """Per-region progress trend. region='__overall__' (default) = nationwide."""
    data = fetch_one_json(PROGRESS_SQL, {"region": region})
    if data is None or data.get("current_rate") is None:
        raise HTTPException(status_code=404, detail=f"no history for region={region!r}")
    return data


@router.get("/cities")
def cities(region: str | None = None):
    return fetch_one_json(CITIES_SQL, {"region": region})


@router.get("/cities.geojson")
def cities_geojson():
    return JSONResponse(fetch_one_json(GEOJSON_SQL))


@router.get("/wards")
def wards():
    return fetch_one_json(WARDS_SQL)


@router.get("/cities/{city_code}")
def city(city_code: str):
    data = fetch_one_json(CITY_ONE_SQL, {"code": city_code})
    if data is None:
        raise HTTPException(status_code=404, detail=f"city_code {city_code} not found")
    return data


app = FastAPI(title="Rapid Plateau Dashboard API", version="0.1")
# CORS applies only to this standalone `app` (dev / `uvicorn dashboard_api:app`);
# in production the router is mounted same-origin behind nginx, so CORS is moot.
# Read-only public data, so origins default to "*"; set DASH_CORS_ORIGINS to restrict.
_cors_origins = [o.strip() for o in os.environ.get("DASH_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["GET"], allow_headers=["*"])
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
