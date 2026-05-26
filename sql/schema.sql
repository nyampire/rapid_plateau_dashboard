-- PLATEAU import-progress dashboard schema (dash_* tables).
-- Idempotent. The dashboard only owns dash_* tables; plateau_* tables are read-only.
-- Apply: psql "$DATABASE_URL" -f sql/schema.sql

-- 4.1 National PLATEAU city master (from attributedata_2025 Excel + OSM wiki).
CREATE TABLE IF NOT EXISTS dash_city_master (
  city_code         TEXT PRIMARY KEY,                 -- 5-digit
  city_name         TEXT,
  prefecture        TEXT,
  region            TEXT,                             -- 地方
  building_lods     TEXT,                             -- e.g. '1+2+3'
  spec_versions     TEXT,                             -- 'V4' / 'V5'
  plateau_provided  BOOLEAN DEFAULT TRUE,             -- has building data in the master
  in_local_db       BOOLEAN,                          -- present in our plateau_buildings
  -- OSM wiki imports_list derived
  osm_import_status TEXT,                             -- not_started / in_progress / done
  osm_import_date   DATE,                             -- wiki completion date
  osm_validated     BOOLEAN,                          -- wiki note '検証済'
  boundary_geom     GEOMETRY(MultiPolygon, 4326),     -- admin boundary (N03; interim coverage hull). nullable for now.
  updated_at        TIMESTAMPTZ DEFAULT now()
);

-- 4.2 OSM building cache (from public extract; created during PoC).
CREATE TABLE IF NOT EXISTS dash_osm_buildings (
  id          BIGSERIAL PRIMARY KEY,
  city_code   TEXT,
  osm_type    CHAR(1),            -- 'w' / 'r'
  osm_id      BIGINT,
  geom        GEOMETRY(Geometry, 4326),
  fetched_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dash_osm_buildings_geom_idx ON dash_osm_buildings USING GIST (geom);
CREATE INDEX IF NOT EXISTS dash_osm_buildings_city_idx ON dash_osm_buildings (city_code);

-- 4.3 Per-city stats snapshot.
CREATE TABLE IF NOT EXISTS dash_city_stats (
  city_code          TEXT PRIMARY KEY REFERENCES dash_city_master(city_code),
  plateau_count      INTEGER,        -- outline only
  osm_count          INTEGER,        -- OSM buildings within coverage
  intersecting_count INTEGER,        -- PLATEAU outlines with an intersecting OSM building
  import_rate        NUMERIC(5,2),   -- intersecting / plateau * 100
  computed_at        TIMESTAMPTZ
);

-- 4.4 Overall progress time series (trend).
CREATE TABLE IF NOT EXISTS dash_progress_history (
  computed_at        TIMESTAMPTZ PRIMARY KEY,
  total_plateau      BIGINT,
  total_intersecting BIGINT,
  overall_rate       NUMERIC(5,2),   -- building-weighted
  cities_total       INTEGER,        -- denominator (national PLATEAU cities)
  cities_in_db       INTEGER,        -- ingested into our DB
  cities_osm_done    INTEGER         -- wiki-'done' cities
);
