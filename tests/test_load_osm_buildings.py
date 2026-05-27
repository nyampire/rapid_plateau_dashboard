"""Integration test for load_osm_buildings.decode_select_sql.

Verifies the osmium id decode ('a<num>' -> even=way/odd=relation, osm_id=num//2)
and city_code assignment via the containing plateau_coverage polygon, and that
buildings outside every coverage polygon are dropped.
"""
import load_osm_buildings as lo


def test_decode_and_coverage_assignment(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO plateau_coverage(city_code, geom) VALUES "
            "('A', ST_GeomFromText('POLYGON((-0.01 -0.01,-0.01 0.01,0.01 0.01,0.01 -0.01,-0.01 -0.01))',4326)),"
            "('B', ST_GeomFromText('POLYGON((0.99 0.99,0.99 1.01,1.01 1.01,1.01 0.99,0.99 0.99))',4326))")

        # staging table as ogr2ogr would create it: text id + geometry
        cur.execute("CREATE TEMP TABLE dash_load_tmp(id text, geom geometry(Geometry,4326))")
        cur.execute(
            "INSERT INTO dash_load_tmp(id, geom) VALUES "
            "('a100', ST_GeomFromText('POLYGON((0 0,0 0.0005,0.0005 0.0005,0.0005 0,0 0))',4326)),"      # in A; even -> way, id 50
            "('a101', ST_GeomFromText('POLYGON((1 1,1 1.0005,1.0005 1.0005,1.0005 1,1 1))',4326)),"      # in B; odd -> relation, id 50
            "('a200', ST_GeomFromText('POLYGON((5 5,5 5.001,5.001 5.001,5.001 5,5 5))',4326))")          # outside coverage -> dropped

        cur.execute("CREATE TEMP TABLE _out AS " + lo.decode_select_sql("dash_load_tmp"))
        cur.execute("SELECT city_code, osm_type, osm_id FROM _out ORDER BY city_code")
        rows = cur.fetchall()

    assert rows == [("A", "w", 50), ("B", "r", 50)]
