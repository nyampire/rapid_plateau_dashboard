"""Shared test fixtures.

Unit tests (parse_sections / ogr_pg / resolve_city_code) need no DB.

Integration tests need a PostGIS database, provided via DASH_TEST_DATABASE_URL
(e.g. `createdb dash_test && psql -d dash_test -c 'CREATE EXTENSION postgis'`,
then `DASH_TEST_DATABASE_URL=postgresql:///dash_test pytest`). When it is unset,
the `db` fixture skips, so the suite stays green without a database.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "api"))

TEST_DB_URL = os.environ.get("DASH_TEST_DATABASE_URL")

# Minimal stand-ins for the plateau_* tables (the real ones live in rapid_plateau_api).
# Only the columns the dashboard queries read.
_PLATEAU_FIXTURE_DDL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS plateau_buildings (
  id            bigserial PRIMARY KEY,
  city_code     text,
  building_part text,
  geom          geometry(Geometry, 4326)
);
CREATE INDEX IF NOT EXISTS plateau_buildings_geom_idx ON plateau_buildings USING GIST (geom);
CREATE INDEX IF NOT EXISTS plateau_buildings_city_idx ON plateau_buildings (city_code);
CREATE TABLE IF NOT EXISTS plateau_coverage (
  city_code text,
  geom      geometry(Geometry, 4326)
);
CREATE INDEX IF NOT EXISTS plateau_coverage_geom_idx ON plateau_coverage USING GIST (geom);
"""

_FIXTURE_TABLES = [
    "plateau_buildings", "plateau_coverage",
    "dash_osm_buildings", "dash_city_stats", "dash_progress_history", "dash_city_master",
]


@pytest.fixture(scope="session")
def db_url():
    if not TEST_DB_URL:
        pytest.skip("DASH_TEST_DATABASE_URL not set; skipping DB integration tests")
    return TEST_DB_URL


@pytest.fixture(scope="session")
def _schema(db_url):
    import psycopg2
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_PLATEAU_FIXTURE_DDL)
            cur.execute((ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
    finally:
        conn.close()
    return True


@pytest.fixture
def db(db_url, _schema):
    """A clean-slate autocommit connection (truncates fixture + dash_* tables)."""
    import psycopg2
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(_FIXTURE_TABLES) + " CASCADE;")
    yield conn
    conn.close()
