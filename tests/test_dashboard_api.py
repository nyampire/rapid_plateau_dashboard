"""Integration test for the read-only dashboard API endpoints (FastAPI TestClient)."""
import pytest


@pytest.fixture
def client(db, db_url, monkeypatch):
    monkeypatch.setenv("DASH_DATABASE_URL", db_url)  # fetch_one_json reads this at call time
    import dashboard_api
    from fastapi.testclient import TestClient

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO dash_city_master(city_code,city_name,prefecture,region,in_local_db,osm_import_status,repr_point) VALUES "
            "('11230','新座市','埼玉県','関東',true,'done', ST_SetSRID(ST_MakePoint(139.565432,35.793210),4326)),"
            "('13308','奥多摩町','東京都','関東',true,'not_started', NULL)")
        cur.execute(
            "INSERT INTO dash_city_stats(city_code,plateau_count,osm_count,intersecting_count,import_rate,computed_at) "
            "VALUES ('11230',58804,52864,52864,89.90, now())")
        cur.execute(
            "INSERT INTO dash_progress_history(computed_at,total_plateau,total_intersecting,overall_rate,"
            "cities_total,cities_in_db,cities_osm_done) VALUES (now(),100,52,52.0,306,144,25)")
        cur.execute(
            "INSERT INTO plateau_coverage(city_code,geom) VALUES "
            "('11230', ST_GeomFromText('POLYGON((139 35,139 35.1,139.1 35.1,139.1 35,139 35))',4326))")
    return TestClient(dashboard_api.app)


def test_summary(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    j = r.json()
    # cities_total is computed live from dash_city_master (2 fixture rows),
    # not read from dash_progress_history.
    assert j["cities_total"] == 2
    assert j["cities_osm_done"] == 1
    assert j["overall_rate"] is not None


def test_regions(client):
    r = client.get("/api/dashboard/regions")
    assert r.status_code == 200
    assert any(x["region"] == "関東" for x in r.json())


def test_cities_list_and_single_and_404(client):
    r = client.get("/api/dashboard/cities")
    assert r.status_code == 200
    by_code = {c["city_code"]: c for c in r.json()}
    assert "11230" in by_code
    # repr_lat/lon come from ST_PointOnSurface in production; here we seed a
    # MakePoint so the values round-trip the API and reach the drawer URL.
    assert float(by_code["11230"]["repr_lat"]) == pytest.approx(35.793210)
    assert float(by_code["11230"]["repr_lon"]) == pytest.approx(139.565432)
    # repr_point IS NULL → frontend falls back to the name-search URL.
    assert by_code["13308"]["repr_lat"] is None
    assert by_code["13308"]["repr_lon"] is None

    r1 = client.get("/api/dashboard/cities/11230")
    assert r1.status_code == 200
    assert r1.json()["city_name"] == "新座市"
    assert float(r1.json()["repr_lat"]) == pytest.approx(35.793210)

    assert client.get("/api/dashboard/cities/00000").status_code == 404


def test_geojson(client):
    r = client.get("/api/dashboard/cities.geojson")
    assert r.status_code == 200
    assert r.json()["type"] == "FeatureCollection"


def test_wards(client, db):
    # Seed a ward under 11230 (新座市 is not actually a designated city; we just
    # reuse the existing fixture's parent code to keep the test self-contained).
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO dash_ward_master(ward_code,parent_city_code,ward_name,repr_point) "
            "VALUES ('14101','11230','鶴見区', ST_SetSRID(ST_MakePoint(139.68,35.50),4326))")
        cur.execute(
            "INSERT INTO dash_ward_stats(ward_code,plateau_count,osm_count,intersecting_count,import_rate,computed_at) "
            "VALUES ('14101', 12345, 6000, 5000, 40.50, now())")
    r = client.get("/api/dashboard/wards")
    assert r.status_code == 200
    j = r.json()
    assert len(j) == 1
    w = j[0]
    assert w["ward_code"] == "14101"
    assert w["parent_city_code"] == "11230"
    assert w["ward_name"] == "鶴見区"
    assert float(w["repr_lat"]) == pytest.approx(35.50)
    assert float(w["repr_lon"]) == pytest.approx(139.68)
    assert w["plateau_count"] == 12345
    assert w["intersecting_count"] == 5000


def test_progress_overall_default_and_region_filter(client, db):
    # Reset history so we own the entire timeline and assertions don't depend on
    # the client fixture's one-row seed.
    with db.cursor() as cur:
        cur.execute("TRUNCATE dash_progress_history;")
        cur.execute("""
            INSERT INTO dash_progress_history
              (computed_at, region, total_plateau, total_intersecting, overall_rate,
               cities_total, cities_in_db, cities_osm_done) VALUES
              (now() - interval '7 days', '関東',        500, 250, 50.00, 100, 80, 20),
              (now() - interval '7 days', '中部',        300, 120, 40.00,  50, 40, 10),
              (now() - interval '7 days', '__overall__', 800, 370, 46.25, 150, 120, 30),
              (now(),                      '関東',        500, 300, 60.00, 100, 80, 20),
              (now(),                      '中部',        300, 150, 50.00,  50, 40, 10),
              (now(),                      '__overall__', 800, 450, 56.25, 150, 120, 30);
        """)

    # Default region = '__overall__': returns the nationwide trend.
    j = client.get("/api/dashboard/progress").json()
    assert j["region"] == "__overall__"
    assert float(j["current_rate"]) == 56.25
    assert float(j["prev_rate"]) == 46.25
    assert len(j["trend"]) == 2

    # Region filter returns only that region's series, no overall leakage.
    j = client.get("/api/dashboard/progress?region=関東").json()
    assert j["region"] == "関東"
    assert float(j["current_rate"]) == 60.00
    assert float(j["prev_rate"]) == 50.00
    assert j["cities_total"] == 100
    assert len(j["trend"]) == 2
    rates = [float(t["rate"]) for t in j["trend"]]
    assert rates == [50.00, 60.00]  # ordered ASC by computed_at

    # Unknown region → 404.
    assert client.get("/api/dashboard/progress?region=does_not_exist").status_code == 404


def test_summary_still_reads_overall_after_region_split(client, db):
    """Sanity check: after issue #14, /summary must keep filtering history to
    region='__overall__' so per-region rows don't leak into the headline rate."""
    with db.cursor() as cur:
        # Newer per-region row with a wildly different rate — must NOT win.
        cur.execute("""
            INSERT INTO dash_progress_history
              (computed_at, region, total_plateau, total_intersecting, overall_rate,
               cities_total, cities_in_db, cities_osm_done)
            VALUES (now() + interval '1 second', '関東', 1, 1, 99.99, 1, 1, 1);
        """)
    j = client.get("/api/dashboard/summary").json()
    assert float(j["overall_rate"]) == 52.0  # the fixture's __overall__ row


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
