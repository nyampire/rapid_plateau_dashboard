"""Integration tests for load_osm_buildings.decode_select_sql and replace_region_rows.

Verifies the osmium id decode ('a<num>' -> even=way/odd=relation, osm_id=num//2)
and city_code assignment via the containing admin polygon (N03 boundary_geom
preferred, plateau_coverage hull as fallback), and that buildings outside every
admin polygon are dropped.
Also verifies that a region load replaces only the rows that region produced,
and that a building appearing in two regions is kept as one row.
"""
from decimal import Decimal

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


def _staging(cur, name):
    """_decoded と同じ形の一時表を作る。"""
    cur.execute(f"CREATE TEMP TABLE {name}("
                "city_code text, osm_type char(1), osm_id bigint, "
                "geom geometry(Geometry,4326))")


def _add(cur, name, city, osm_type, osm_id, offset_deg):
    """一時表に建物を 1 件足す。offset_deg で位置をずらす。"""
    cur.execute(
        f"INSERT INTO {name}(city_code, osm_type, osm_id, geom) "
        "SELECT %s, %s, %s, ST_Translate("
        "  ST_GeomFromText('POLYGON((0 0,0 0.0005,0.0005 0.0005,0.0005 0,0 0))',4326),"
        "  %s, 0)",
        (city, osm_type, osm_id, offset_deg))


def test_other_regions_rows_survive(db):
    """後から読む地域の抽出に 1 件はみ出していても、先の地域が入れた行は残る。

    これが 2026-09-11 に見つかった不具合の再現である。
    いわき市は東北の抽出で 14 万件入ったあと、関東の抽出に南端の数百件が
    含まれていたため、市区町村コード単位の削除で 14 万件が消えていた。
    """
    with db.cursor() as cur:
        _staging(cur, "d_tohoku")
        for i in range(1, 101):
            _add(cur, "d_tohoku", "A", "w", i, i * 0.001)
        lo.replace_region_rows(cur, "tohoku", "d_tohoku")

        _staging(cur, "d_kanto")
        _add(cur, "d_kanto", "A", "w", 9001, 0.5)
        lo.replace_region_rows(cur, "kanto", "d_kanto")

        cur.execute("SELECT count(*) FROM dash_osm_buildings WHERE city_code='A'")
        assert cur.fetchone()[0] == 101


def test_border_building_is_stored_once(db):
    """2 つの地域の抽出に同じ建物が現れても 1 行になる。

    所属は後から読んだ地域に移り、形と市区町村コードも新しいほうで上書きされる。
    翌週にその地域が消して入れ直すので、行は毎週更新される。
    """
    with db.cursor() as cur:
        _staging(cur, "d_a")
        _add(cur, "d_a", "A", "w", 42, 0.0)
        lo.replace_region_rows(cur, "kanto", "d_a")

        _staging(cur, "d_b")
        _add(cur, "d_b", "B", "w", 42, 0.5)
        lo.replace_region_rows(cur, "chubu", "d_b")

        cur.execute("SELECT count(*) FROM dash_osm_buildings")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT source_region, city_code, round(ST_X(ST_Centroid(geom))::numeric, 3) "
                    "FROM dash_osm_buildings")
        assert cur.fetchone() == ("chubu", "B", Decimal("0.500"))


def test_region_reload_drops_vanished_buildings(db):
    """同じ地域を読み直すと、抽出から消えた建物は残らない。"""
    with db.cursor() as cur:
        _staging(cur, "d_first")
        _add(cur, "d_first", "A", "w", 1, 0.0)
        _add(cur, "d_first", "A", "w", 2, 0.01)
        lo.replace_region_rows(cur, "kanto", "d_first")

        _staging(cur, "d_second")
        _add(cur, "d_second", "A", "w", 1, 0.0)
        lo.replace_region_rows(cur, "kanto", "d_second")

        cur.execute("SELECT osm_id FROM dash_osm_buildings ORDER BY osm_id")
        assert cur.fetchall() == [(1,)]
