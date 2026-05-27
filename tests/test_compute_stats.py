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
