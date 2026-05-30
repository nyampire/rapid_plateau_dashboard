"""Integration test for the import-rate intersection criterion (compute_stats.STATS_SQL).

Verifies: denominator = outlines only (building_part IS NULL); a PLATEAU outline
counts as present when its representative point is inside an OSM building OR the
area overlap exceeds 30%.
"""
import compute_stats

CITY = "99999"


def _plateau(cur, ox, oy, s=0.001, part=None):
    wkt = f"POLYGON(({ox} {oy},{ox} {oy+s},{ox+s} {oy+s},{ox+s} {oy},{ox} {oy}))"
    cur.execute("INSERT INTO plateau_buildings(city_code, building_part, geom) "
                "VALUES (%s,%s, ST_GeomFromText(%s,4326))", (CITY, part, wkt))


def _osm(cur, x0, y0, x1, y1):
    wkt = f"POLYGON(({x0} {y0},{x0} {y1},{x1} {y1},{x1} {y0},{x0} {y0}))"
    cur.execute("INSERT INTO dash_osm_buildings(city_code, osm_type, osm_id, geom) "
                "VALUES (%s,'w',1, ST_GeomFromText(%s,4326))", (CITY, wkt))


def test_intersection_criterion(db):
    with db.cursor() as cur:
        # B1 @0.00: OSM fully contains the outline -> match (point-in)
        _plateau(cur, 0.00, 0.0)
        _osm(cur, -0.0005, -0.0005, 0.0015, 0.0015)
        # B2 @0.01: OSM covers left 40% (>30%) but not the centre -> match (area)
        _plateau(cur, 0.01, 0.0)
        _osm(cur, 0.01, 0.0, 0.0104, 0.001)
        # B3 @0.02: OSM covers left 10% only -> no match
        _plateau(cur, 0.02, 0.0)
        _osm(cur, 0.02, 0.0, 0.0201, 0.001)
        # B4 @0.03: no OSM at all -> no match
        _plateau(cur, 0.03, 0.0)
        # B5 @0.04: a building:part fully covered by OSM -> excluded from denominator
        _plateau(cur, 0.04, 0.0, part="yes")
        _osm(cur, 0.0395, -0.0005, 0.0415, 0.0015)

        cur.execute(compute_stats.STATS_SQL, {"city": CITY})
        plateau_count, intersecting = cur.fetchone()

    assert plateau_count == 4          # B1..B4 outlines; B5 part excluded
    assert intersecting == 2           # B1 (point-in), B2 (area>30%)


# Ward-level fixture / test. Verifies that compute_stats spatially filters
# plateau_buildings and dash_osm_buildings by the ward boundary so designated
# cities can be drilled into.
PARENT = "88888"
WARD = "88801"


def _ward_setup(cur):
    cur.execute("INSERT INTO dash_city_master(city_code, city_name) VALUES (%s, '親市')", (PARENT,))
    # Ward boundary: 0..0.01 in x and y. Buildings outside this box belong to the
    # parent city but to a different ward (not under test).
    wkt = ("MULTIPOLYGON((("
           "0 0, 0 0.01, 0.01 0.01, 0.01 0, 0 0)))")
    cur.execute("INSERT INTO dash_ward_master(ward_code, parent_city_code, ward_name, boundary_geom) "
                "VALUES (%s, %s, '区A', ST_GeomFromText(%s, 4326))", (WARD, PARENT, wkt))


def _parent_plateau(cur, ox, oy, s=0.001, part=None):
    wkt = f"POLYGON(({ox} {oy},{ox} {oy+s},{ox+s} {oy+s},{ox+s} {oy},{ox} {oy}))"
    cur.execute("INSERT INTO plateau_buildings(city_code, building_part, geom) "
                "VALUES (%s,%s, ST_GeomFromText(%s,4326))", (PARENT, part, wkt))


def _parent_osm(cur, x0, y0, x1, y1, city=PARENT):
    wkt = f"POLYGON(({x0} {y0},{x0} {y1},{x1} {y1},{x1} {y0},{x0} {y0}))"
    cur.execute("INSERT INTO dash_osm_buildings(city_code, osm_type, osm_id, geom) "
                "VALUES (%s,'w',1, ST_GeomFromText(%s,4326))", (city, wkt))


def test_ward_stats_filters_by_boundary(db):
    with db.cursor() as cur:
        _ward_setup(cur)
        # WB1: PLATEAU inside ward, OSM contains -> ward match
        _parent_plateau(cur, 0.001, 0.001)
        _parent_osm(cur, 0.0005, 0.0005, 0.0025, 0.0025)
        # WB2: PLATEAU inside ward, no OSM -> denominator only
        _parent_plateau(cur, 0.003, 0.003)
        # WB3: PLATEAU outside ward (parent city's, but in another ward) -> excluded entirely
        _parent_plateau(cur, 0.02, 0.02)
        _parent_osm(cur, 0.019, 0.019, 0.022, 0.022)
        # WB4: building:part inside ward -> excluded from denominator
        _parent_plateau(cur, 0.004, 0.001, part="yes")
        # OSM-only inside ward (no PLATEAU here) — only affects ward osm_count
        _parent_osm(cur, 0.006, 0.006, 0.007, 0.007)
        # OSM inside ward but assigned to a different parent city -> excluded
        _parent_osm(cur, 0.0061, 0.0061, 0.0069, 0.0069, city="77777")

        cur.execute(compute_stats.WARD_STATS_SQL, {"ward": WARD})
        plateau_count, intersecting = cur.fetchone()
        cur.execute(compute_stats.WARD_OSM_COUNT_SQL, {"ward": WARD})
        osm_count = cur.fetchone()[0]

    assert plateau_count == 2   # WB1, WB2 (WB3 outside ward, WB4 is a part)
    assert intersecting == 1    # WB1 only
    assert osm_count == 2       # WB1's covering OSM + lone in-ward OSM; the 77777-parent one is excluded
