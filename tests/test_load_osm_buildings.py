"""Integration test for load_osm_buildings.decode_select_sql.

Verifies the osmium id decode ('a<num>' -> even=way/odd=relation, osm_id=num//2)
and city_code assignment via the containing admin polygon (N03 boundary_geom
preferred, plateau_coverage hull as fallback), and that buildings outside every
admin polygon are dropped.
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


def test_boundary_preferred_over_coverage(db):
    """N03 boundary_geom wins over an overlapping coverage hull; coverage is the
    fallback only where no boundary contains the point."""
    with db.cursor() as cur:
        # Boundary 'N' and coverage 'C' overlap the same area around (0,0);
        # coverage 'D' around (2,2) has no boundary.
        cur.execute(
            "INSERT INTO dash_city_master(city_code, boundary_geom) VALUES "
            "('N', ST_Multi(ST_GeomFromText('POLYGON((-0.01 -0.01,-0.01 0.01,0.01 0.01,0.01 -0.01,-0.01 -0.01))',4326)))")
        cur.execute(
            "INSERT INTO plateau_coverage(city_code, geom) VALUES "
            "('C', ST_GeomFromText('POLYGON((-0.02 -0.02,-0.02 0.02,0.02 0.02,0.02 -0.02,-0.02 -0.02))',4326)),"
            "('D', ST_GeomFromText('POLYGON((1.99 1.99,1.99 2.01,2.01 2.01,2.01 1.99,1.99 1.99))',4326))")

        cur.execute("CREATE TEMP TABLE dash_load_tmp(id text, geom geometry(Geometry,4326))")
        cur.execute(
            "INSERT INTO dash_load_tmp(id, geom) VALUES "
            "('a10', ST_GeomFromText('POLYGON((0 0,0 0.0005,0.0005 0.0005,0.0005 0,0 0))',4326)),"      # in N boundary AND C coverage -> N
            "('a12', ST_GeomFromText('POLYGON((2 2,2 2.0005,2.0005 2.0005,2.0005 2,2 2))',4326))")      # only in D coverage -> D fallback

        cur.execute("CREATE TEMP TABLE _out AS " + lo.decode_select_sql("dash_load_tmp"))
        cur.execute("SELECT city_code, osm_id FROM _out ORDER BY city_code")
        rows = cur.fetchall()

    assert rows == [("D", 6), ("N", 5)]
