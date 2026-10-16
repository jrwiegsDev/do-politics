"""Tests for serving photos, and for the photos in the officials and member tiles."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import JPEG, REP_13, SENATOR_A, add, add_photo

from app.main import app, get_conn
from app.photos import etag_for, etag_matches, find_photo
from ingest.members import UPSERT_SQL as UPSERT_MEMBER_SQL
from ingest.photos import to_row

CAPITOL = (-89.6548, 39.7984)
ADDRESS = {
    "street": "401 S 2nd St",
    "city": "Springfield",
    "state": "IL",
    "zip_code": "62701",
}
SHA = to_row("T000012", JPEG, "unitedstates", "https://example.test", None)["sha256"]
CREDIT = "Courtesy U.S. House Office"


def seat(conn, row) -> None:
    """Seat a made-up official with a term running whenever the tests run.

    The official is added to members too, because a photo belongs to a member.
    """
    member = {
        "bioguide_id": row["bioguide_id"],
        "lis_id": None,
        "name": row["name"],
        "source": "legislators-current",
    }
    conn.execute(UPSERT_MEMBER_SQL, member)
    add(conn, row | {"term_end": date(2099, 1, 3)})


@pytest.fixture
def client(conn):
    """A client whose requests use the test database."""
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def geocode_to_the_capitol(monkeypatch) -> None:
    monkeypatch.setattr("app.pages.geocode", lambda address: CAPITOL)
    monkeypatch.setattr("app.main.geocode", lambda address: CAPITOL)


@pytest.mark.parametrize(
    ("header", "matches"),
    [
        (None, False),
        (f'"{SHA}"', True),
        (f'W/"{SHA}"', True),
        (f'"old", "{SHA}"', True),
        ("*", True),
        ('"old"', False),
        # Without its quotes it is not the same ETag.
        (SHA, False),
    ],
)
def test_etag_matches_reads_every_form_of_the_header(header, matches):
    assert etag_matches(header, SHA) is matches


def test_find_photo_returns_the_image_and_its_fingerprint(conn):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])

    assert find_photo(conn, REP_13["bioguide_id"]) == (JPEG, SHA)


def test_find_photo_returns_none_when_none_is_stored(conn):
    assert find_photo(conn, "T000012") is None


def test_a_photo_is_served_as_a_jpeg_with_its_etag_and_cache_time(client, conn):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])

    response = client.get("/photos/T000012.jpg")

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == etag_for(SHA)
    assert response.headers["cache-control"] == "public, max-age=86400"


def test_a_browser_holding_the_photo_gets_304_and_no_image(client, conn):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])

    response = client.get(
        "/photos/T000012.jpg", headers={"If-None-Match": etag_for(SHA)}
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag_for(SHA)


def test_a_browser_holding_an_old_photo_gets_the_new_one(client, conn):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])

    response = client.get("/photos/T000012.jpg", headers={"If-None-Match": '"old"'})

    assert response.status_code == 200
    assert response.content == JPEG


def test_a_member_with_no_photo_gets_404(client):
    assert client.get("/photos/T000012.jpg").status_code == 404


def test_an_id_that_is_not_a_bioguide_id_gets_422(client):
    assert client.get("/photos/nobody.jpg").status_code == 422


def test_officials_tile_shows_a_stored_photo_and_a_placeholder_for_the_rest(
    client, conn, monkeypatch
):
    seat(conn, SENATOR_A)
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])
    geocode_to_the_capitol(monkeypatch)

    page = client.post("/tiles/officials", data=ADDRESS).text

    assert 'src="/photos/T000012.jpg"' in page
    assert 'src="/photos/T000010.jpg"' not in page
    assert 'src="/static/no-photo.svg"' in page


def test_a_photo_has_empty_alt_text_because_the_name_is_beside_it(
    client, conn, monkeypatch
):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])
    geocode_to_the_capitol(monkeypatch)

    page = client.post("/tiles/officials", data=ADDRESS).text

    assert 'alt=""' in page


def test_member_tile_credits_a_photo_that_asks_for_it(client, conn):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"], attribution=CREDIT)

    page = client.get("/tiles/members/T000012").text

    assert 'src="/photos/T000012.jpg"' in page
    assert f"Photo: {CREDIT}" in page


def test_member_tile_shows_no_credit_line_for_a_public_domain_photo(client, conn):
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"])

    page = client.get("/tiles/members/T000012").text

    assert "Photo:" not in page


def test_lookup_says_which_officials_have_a_photo(client, conn, monkeypatch):
    seat(conn, SENATOR_A)
    seat(conn, REP_13)
    add_photo(conn, REP_13["bioguide_id"], attribution=CREDIT)
    geocode_to_the_capitol(monkeypatch)

    body = client.post("/lookup", json=ADDRESS).json()

    assert body["senators"][0]["has_photo"] is False
    assert body["senators"][0]["photo_credit"] is None
    assert body["representative"]["has_photo"] is True
    assert body["representative"]["photo_credit"] == CREDIT
