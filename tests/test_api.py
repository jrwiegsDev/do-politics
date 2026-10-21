"""Tests for the API. The geocoder is replaced, so no test calls the Census."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import (
    REP_13,
    SENATOR_A,
    SENATOR_B,
    add,
    add_bill,
    add_generated,
    add_members,
    add_vote,
    summary,
)

from app.geocoder import GeocoderUnavailable
from app.main import app, get_conn

CAPITOL = (-89.6548, 39.7984)
MID_ATLANTIC = (-40.0, 35.0)
ADDRESS = {
    "street": "401 S 2nd St",
    "city": "Springfield",
    "state": "IL",
    "zip_code": "62701",
}


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


def test_street_that_is_too_short_is_rejected(client):
    response = client.post("/lookup", json={"street": "x", "zip_code": "62701"})

    assert response.status_code == 422


def test_street_with_no_zip_code_or_city_and_state_is_rejected(client):
    response = client.post("/lookup", json={"street": "401 S 2nd St"})

    assert response.status_code == 422


def test_street_and_zip_code_alone_are_accepted(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post(
        "/lookup", json={"street": "401 S 2nd St", "zip_code": "62701"}
    )

    assert response.status_code == 200


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


def test_member_votes_carry_the_bill_each_one_is_about(client, conn):
    add_members(conn)
    add_bill(conn)
    add_vote(conn, 1, day=5, positions={"T000001": "Yea"})

    body = client.get("/members/T000001/votes").json()

    assert body["votes"][0]["bill"] == {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 9999,
        "title": "Example Act",
        "policy_area": "Energy",
        "source_url": "https://example.test/bill/9999",
    }


def test_bill_returns_the_bill_and_its_summaries_newest_stage_first(client, conn):
    add_members(conn)
    add_bill(
        conn,
        summaries=[
            summary("00", date(2025, 1, 15), "Introduced in House", "As introduced."),
            summary("49", date(2025, 5, 9), "Public Law", "As enacted."),
        ],
    )

    response = client.get("/bills/119/hr/9999")

    assert response.status_code == 200
    assert response.json() == {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 9999,
        "title": "Example Act",
        "policy_area": "Energy",
        "sponsor_bioguide_id": "T000001",
        "sponsor_name": "Pat Example",
        "introduced_on": "2025-01-15",
        "latest_action_on": "2025-05-09",
        "latest_action": "Became Public Law.",
        "source_url": "https://example.test/bill/9999",
        "summaries": [
            {
                "version_code": "49",
                "action_on": "2025-05-09",
                "action_desc": "Public Law",
                "summary_text": "As enacted.",
            },
            {
                "version_code": "00",
                "action_on": "2025-01-15",
                "action_desc": "Introduced in House",
                "summary_text": "As introduced.",
            },
        ],
    }


def test_bill_with_no_official_summary_has_an_empty_list(client, conn):
    add_members(conn)
    add_bill(conn)

    response = client.get("/bills/119/hr/9999")

    assert response.status_code == 200
    assert response.json()["summaries"] == []


def test_bill_does_not_send_the_generated_summary_yet(client, conn):
    # The bill tile shows an approved generated summary; the API is left as it
    # was until it is decided how the API should label one.
    add_members(conn)
    add_bill(
        conn,
        summaries=[summary("00", date(2025, 1, 15), "Introduced in House", "CRS.")],
    )
    add_generated(conn, "Plain words")

    body = client.get("/bills/119/hr/9999").json()

    assert "generated" not in body
    assert "Plain words" not in str(body)


def test_bill_never_sends_the_original_html_even_if_the_query_returns_it(
    client, conn, monkeypatch
):
    # The response's declared shape is the last guard. Here the lookup is
    # made to hand over the stored HTML and the database's own id, as a
    # careless change to the query might, and neither may be sent.
    add_members(conn)
    add_bill(conn)
    leaky = {
        "id": 1,
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 9999,
        "title": "Example Act",
        "policy_area": None,
        "sponsor_bioguide_id": "T000001",
        "sponsor_name": "Pat Example",
        "introduced_on": date(2025, 1, 15),
        "latest_action_on": None,
        "latest_action": None,
        "source_url": "https://example.test/bill/9999",
        "summary_html_source": "<p>Top level.</p>",
        "summaries": [
            summary("49", date(2025, 5, 9), "Public Law", "As enacted.")
            | {"bill_id": 1}
        ],
    }
    monkeypatch.setattr("app.main.find_bill", lambda *args: leaky)

    response = client.get("/bills/119/hr/9999")

    assert response.status_code == 200
    assert "summary_html_source" not in response.text
    assert "<p>" not in response.text
    assert set(response.json()["summaries"][0]) == {
        "version_code",
        "action_on",
        "action_desc",
        "summary_text",
    }
    assert "id" not in response.json()


def test_unknown_bill_is_404(client):
    response = client.get("/bills/119/hr/1")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/members/not-an-id/votes",
        "/members/T000001/votes?limit=0",
        "/members/T000001/votes?limit=101",
        "/members/T000001/votes?show=some",
        "/bills/119/bill/1",
        "/bills/119/HR/1",
        "/bills/0/hr/1",
        "/bills/119/hr/0",
        "/bills/119/hr/one",
    ],
)
def test_malformed_requests_are_rejected(client, path):
    response = client.get(path)

    assert response.status_code == 422
