"""Tests for the API. The geocoder is replaced, so no test calls the Census."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import REP_13, SENATOR_A, SENATOR_B, add, add_members, add_vote

from app.geocoder import GeocoderUnavailable
from app.main import app, get_conn

CAPITOL = (-89.6548, 39.7984)
MID_ATLANTIC = (-40.0, 35.0)
ADDRESS = {"address": "401 S 2nd St, Springfield, IL 62701"}


def seated(row):
    """Give a made-up official a term that is still running whenever the tests run."""
    return row | {"term_end": date(2099, 1, 3)}


@pytest.fixture
def client(conn):
    """An API client whose requests use the test database."""
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def geocode_to(monkeypatch, point):
    """Make the API's geocoder return a fixed point (or None) for any address."""
    monkeypatch.setattr("app.main.geocode", lambda address: point)


def test_health_reports_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lookup_returns_the_district_senators_and_representative(
    client, conn, monkeypatch
):
    add(conn, seated(SENATOR_A), seated(SENATOR_B), seated(REP_13))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post("/lookup", json=ADDRESS)
    body = response.json()

    assert response.status_code == 200
    assert body["district"] == {
        "state_fips": "17",
        "code": "13",
        "name": "Congressional District 13",
    }
    assert [s["name"] for s in body["senators"]] == ["Alex Senator", "Blake Senator"]
    assert body["representative"]["name"] == "Casey Rep"


def test_lookup_reports_a_vacant_seat_as_no_representative(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A), seated(SENATOR_B))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post("/lookup", json=ADDRESS)
    body = response.json()

    assert response.status_code == 200
    assert len(body["senators"]) == 2
    assert body["representative"] is None


def test_address_the_geocoder_cannot_match_is_404(client, monkeypatch):
    geocode_to(monkeypatch, None)

    response = client.post("/lookup", json=ADDRESS)

    assert response.status_code == 404


def test_point_outside_every_district_is_404(client, monkeypatch):
    geocode_to(monkeypatch, MID_ATLANTIC)

    response = client.post("/lookup", json=ADDRESS)

    assert response.status_code == 404


def test_geocoder_outage_is_503(client, monkeypatch):
    def unavailable(address):
        raise GeocoderUnavailable("ConnectTimeout")

    monkeypatch.setattr("app.main.geocode", unavailable)

    response = client.post("/lookup", json=ADDRESS)

    assert response.status_code == 503


def test_address_that_is_too_short_is_rejected(client):
    response = client.post("/lookup", json={"address": "x"})

    assert response.status_code == 422


def test_address_cannot_be_sent_in_the_url(client):
    response = client.get("/lookup", params=ADDRESS)

    assert response.status_code == 405


def test_member_votes_returns_the_member_and_their_final_votes(client, conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={"T000001": "Yea"})
    add_vote(
        conn, 2, day=6, positions={"T000001": "Nay"}, question="On Motion to Recommit"
    )

    response = client.get("/members/T000001/votes")
    body = response.json()

    assert response.status_code == 200
    assert body["member"] == {"bioguide_id": "T000001", "name": "Pat Example"}
    assert [
        (v["roll_number"], v["position"], v["category"]) for v in body["votes"]
    ] == [(1, "Yea", "legislation")]


def test_member_votes_gives_the_time_with_its_offset(client, conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={"T000001": "Yea"})

    body = client.get("/members/T000001/votes").json()

    assert body["votes"][0]["voted_at"] in (
        "2026-01-05T12:00:00Z",
        "2026-01-05T12:00:00+00:00",
    )


def test_show_all_adds_procedural_votes(client, conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={"T000001": "Yea"})
    add_vote(
        conn, 2, day=6, positions={"T000001": "Nay"}, question="On Motion to Recommit"
    )

    body = client.get("/members/T000001/votes", params={"show": "all"}).json()

    assert [v["roll_number"] for v in body["votes"]] == [2, 1]


def test_limit_caps_the_number_of_votes(client, conn):
    add_members(conn)
    for roll_number in (1, 2, 3):
        add_vote(conn, roll_number, day=roll_number, positions={"T000001": "Yea"})

    body = client.get("/members/T000001/votes", params={"limit": 2}).json()

    assert [v["roll_number"] for v in body["votes"]] == [3, 2]


def test_member_with_no_votes_gets_an_empty_list(client, conn):
    add_members(conn)

    response = client.get("/members/T000001/votes")

    assert response.status_code == 200
    assert response.json()["votes"] == []


def test_unknown_member_is_404(client):
    response = client.get("/members/Z999999/votes")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/members/not-an-id/votes",
        "/members/T000001/votes?limit=0",
        "/members/T000001/votes?limit=101",
        "/members/T000001/votes?show=some",
    ],
)
def test_malformed_requests_are_rejected(client, path):
    response = client.get(path)

    assert response.status_code == 422
