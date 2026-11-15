"""Tests for the web app's connection pool: opened at startup, lent per request, closed at shutdown."""

from fastapi.testclient import TestClient

from app.deps import POOL_MAX_SIZE
from app.main import app
from ingest.db import conninfo


def test_the_pool_opens_at_startup_and_closes_at_shutdown(database):
    # Used in a with block, TestClient runs the app's lifespan, as uvicorn does.
    with TestClient(app) as client:
        pool = app.state.pool
        assert not pool.closed
        assert client.get("/health").json() == {"status": "ok"}

    assert pool.closed


def test_the_pool_connects_to_the_database_set_when_the_app_starts(database):
    # The tests point POSTGRES_DB at the test database after the app's code
    # is imported, so the pool must read the settings at startup, not import.
    with TestClient(app), app.state.pool.connection() as conn:
        name = conn.execute("SELECT current_database()").fetchone()[0]

    assert name == "dopolitics_test"
    assert "dbname=dopolitics_test" in conninfo()


def test_more_requests_than_the_pool_holds_all_succeed(database):
    # Each request gives its connection back, so the pool never runs dry.
    with TestClient(app) as client:
        for _ in range(POOL_MAX_SIZE * 3):
            assert client.get("/health").status_code == 200

        assert app.state.pool.get_stats()["pool_size"] <= POOL_MAX_SIZE


def test_a_failed_request_gives_its_connection_back(database, monkeypatch):
    # A route that raises must still return its connection, rolled back, so
    # one error can't leak connections until the pool is empty.
    def broken_lookup(conn, bioguide_id):
        conn.execute("SELECT 1")
        raise RuntimeError("something went wrong mid-request")

    monkeypatch.setattr("app.main.find_member", broken_lookup)

    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(POOL_MAX_SIZE + 2):
            response = client.get("/members/T000001/votes")
            assert response.status_code == 500

        assert client.get("/health").status_code == 200
