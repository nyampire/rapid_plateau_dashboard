"""Unit tests for load_osm_buildings.ogr_pg.

Guards the security fix: the DB password must travel via PGPASSWORD in the
environment, never inside the 'PG:' string (which becomes a visible argv).
"""
from load_osm_buildings import ogr_pg


def test_password_goes_to_env_not_connection_string():
    conn, env = ogr_pg("postgresql://alice:s3cret@dbhost:5433/plateau")
    assert "password" not in conn          # not in argv
    assert "s3cret" not in conn
    assert env["PGPASSWORD"] == "s3cret"
    assert "host=dbhost" in conn
    assert "port=5433" in conn
    assert "dbname=plateau" in conn
    assert "user=alice" in conn
    assert conn.startswith("PG:")


def test_defaults_when_url_omits_host_port_and_password(monkeypatch):
    monkeypatch.delenv("PGPASSWORD", raising=False)
    conn, env = ogr_pg("postgresql:///plateau")
    assert "host=localhost" in conn
    assert "port=5432" in conn
    assert "dbname=plateau" in conn
    assert "user=" not in conn             # no username in URL -> no user= token
    assert "PGPASSWORD" not in env         # no password in URL -> not added
