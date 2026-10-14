"""Tests for the pages. The geocoder is replaced, so no test calls the Census."""

import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import REP_13, SENATOR_A, SENATOR_B, add

from app.geocoder import GeocoderUnavailable
from app.main import app, get_conn

CAPITOL = (-89.6548, 39.7984)
MID_ATLANTIC = (-40.0, 35.0)
STREET = "401 S 2nd St"
ADDRESS = {
    "street": STREET,
    "city": "Springfield",
    "state": "IL",
    "zip_code": "62701",
}


def seated(row):
    """Give a made-up official a term that is still running whenever the tests run."""
    return row | {"term_end": date(2099, 1, 3)}


@pytest.fixture
def client(conn):
    """A client whose requests use the test database."""
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def geocode_to(monkeypatch, point):
    """Make the pages' geocoder return a fixed point (or None) for any address."""
    monkeypatch.setattr("app.pages.geocode", lambda address: point)


def test_front_page_has_a_form_that_posts_the_address(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'method="post"' in response.text
    assert 'hx-post="/tiles/officials"' in response.text


@pytest.mark.parametrize(
    ("name", "autocomplete"),
    [
        ("street", "address-line1"),
        ("city", "address-level2"),
        ("state", "address-level1"),
        ("zip_code", "postal-code"),
    ],
)
def test_each_address_field_tells_the_browser_what_it_holds(client, name, autocomplete):
    page = client.get("/").text

    assert f'name="{name}"' in page
    assert f'autocomplete="{autocomplete}"' in page


def test_front_page_asks_no_other_site_for_anything(client):
    page = client.get("/").text

    assert 'src="http' not in page
    assert 'href="http' not in page


def test_stylesheet_and_script_are_served_from_our_own_folder(client):
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/htmx.min.js").status_code == 200


def test_officials_tile_lists_the_senators_and_the_representative(
    client, conn, monkeypatch
):
    add(conn, seated(SENATOR_A), seated(SENATOR_B), seated(REP_13))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert response.status_code == 200
    assert "Congressional District 13" in response.text
    assert "Alex Senator" in response.text
    assert "Blake Senator" in response.text
    assert "Casey Rep" in response.text
    assert "vacant" not in response.text


def test_officials_tile_is_a_fragment_not_a_whole_page(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert "<html" not in response.text


def test_officials_tile_says_when_the_house_seat_is_vacant(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A), seated(SENATOR_B))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert response.status_code == 200
    assert "This House seat is currently vacant." in response.text


def test_a_name_is_shown_as_text_and_never_as_html(client, conn, monkeypatch):
    add(conn, seated(REP_13 | {"name": "<b>Casey</b>"}))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert "&lt;b&gt;Casey&lt;/b&gt;" in response.text
    assert "<b>Casey</b>" not in response.text


def test_address_the_geocoder_cannot_match_gets_a_message_and_404(client, monkeypatch):
    geocode_to(monkeypatch, None)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert response.status_code == 404
    # The apostrophe is escaped in the HTML, so the test checks around it.
    assert "Sorry! We couldn" in response.text
    assert "locate that address. Try typing it in again." in response.text


def test_point_outside_every_district_gets_a_message_and_404(client, monkeypatch):
    geocode_to(monkeypatch, MID_ATLANTIC)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert response.status_code == 404
    assert "That address is not in a congressional district." in response.text


def test_geocoder_outage_gets_a_message_and_503(client, monkeypatch):
    def unavailable(address):
        raise GeocoderUnavailable("ConnectTimeout")

    monkeypatch.setattr("app.pages.geocode", unavailable)

    response = client.post("/tiles/officials", data=ADDRESS)

    assert response.status_code == 503
    assert "The address service is unavailable." in response.text


@pytest.mark.parametrize(
    "fields",
    [
        {"street": "x", "zip_code": "62701"},
        {"street": STREET},
        {"street": STREET, "city": "Springfield"},
        {"street": STREET, "zip_code": "6270"},
        {},
    ],
)
def test_an_address_that_breaks_a_rule_gets_a_message_and_422(client, fields):
    response = client.post("/tiles/officials", data=fields)

    assert response.status_code == 422
    assert "Enter a street address with a ZIP code" in response.text
    assert STREET not in response.text


def test_street_and_zip_code_alone_are_accepted(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A))
    geocode_to(monkeypatch, CAPITOL)

    response = client.post(
        "/tiles/officials", data={"street": STREET, "zip_code": "62701"}
    )

    assert response.status_code == 200
    assert "Alex Senator" in response.text


def test_address_cannot_be_sent_in_the_url(client):
    response = client.get("/tiles/officials", params=ADDRESS)

    assert response.status_code == 405


def test_address_is_never_sent_back_or_logged(client, conn, monkeypatch, caplog):
    add(conn, seated(SENATOR_A), seated(SENATOR_B), seated(REP_13))
    geocode_to(monkeypatch, CAPITOL)

    with caplog.at_level(logging.DEBUG):
        found = client.post("/tiles/officials", data=ADDRESS)
        geocode_to(monkeypatch, None)
        not_found = client.post("/tiles/officials", data=ADDRESS)

    assert STREET not in found.text
    assert STREET not in not_found.text
    assert STREET not in caplog.text
