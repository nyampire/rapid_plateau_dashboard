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


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
