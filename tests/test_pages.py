"""Tests for the pages. The geocoder is replaced, so no test calls the Census."""

import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import (
    REP_13,
    SENATOR_A,
    SENATOR_B,
    add,
    add_bill,
    add_members,
    add_vote,
    official,
)

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


# The member tile. Pat Example is in the made-up members, so votes can be
# stored for that ID, and here also holds a House seat.
PAT = "T000001"
PAT_IN_OFFICE = official(PAT, "Pat Example", "house", "13") | {
    "phone": "202-555-0100",
    "office_address": "1 Example House Office Building",
    "website": "https://example.test/pat",
}


def add_pat(conn):
    """Store Pat Example as a member and as a sitting representative."""
    add_members(conn)
    add(conn, seated(PAT_IN_OFFICE))


def test_each_official_in_the_tile_opens_their_member_tile(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A), seated(REP_13))
    geocode_to(monkeypatch, CAPITOL)

    tile = client.post("/tiles/officials", data=ADDRESS).text

    assert 'hx-get="/tiles/members/T000010"' in tile
    assert 'hx-get="/tiles/members/T000012"' in tile
    assert 'hx-target="#member-tile"' in tile


def test_member_tile_shows_who_they_are_and_how_to_reach_them(client, conn):
    add_pat(conn)

    response = client.get(f"/tiles/members/{PAT}")

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Pat Example" in response.text
    assert "Representative" in response.text
    assert "202-555-0100" in response.text
    assert "1 Example House Office Building" in response.text
    assert 'href="https://example.test/pat"' in response.text


def test_member_tile_never_links_to_something_that_is_not_a_web_address(client, conn):
    add_members(conn)
    add(conn, seated(PAT_IN_OFFICE | {"website": "javascript:alert(1)"}))

    response = client.get(f"/tiles/members/{PAT}")

    assert "javascript:" not in response.text


def test_member_tile_shows_attendance_for_the_session(client, conn):
    add_pat(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={PAT: "Not Voting"})
    add_vote(conn, 3, day=7, positions={PAT: "Nay"})
    add_vote(conn, 4, day=8, positions={PAT: "Yea"})

    tile = " ".join(client.get(f"/tiles/members/{PAT}").text.split())

    assert "Congress 119, session 2: voted in 3 of 4 roll calls (75.0%)" in tile


def test_member_tile_lists_votes_newest_first_with_the_position(client, conn):
    add_pat(conn)
    add_bill(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, title="Older Act")
    add_vote(conn, 2, day=6, positions={PAT: "Nay"}, issue="H R 7777", title="New Act")

    tile = client.get(f"/tiles/members/{PAT}").text

    assert tile.index("New Act") < tile.index("Example Act")
    assert "Nay" in tile
    assert "Yea" in tile
    assert "Jan 6, 2026" in tile


def test_member_tile_offers_each_topic_once(client, conn):
    add_pat(conn)
    add_bill(conn, 9999, policy_area="Health")
    add_bill(conn, 8888, policy_area="Energy")
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 3, day=7, positions={PAT: "Yea"}, issue="H R 8888")

    tile = client.get(f"/tiles/members/{PAT}").text

    assert tile.count('<option value="Health">') == 1
    assert tile.count('<option value="Energy">') == 1
    assert '<option value="">All topics</option>' in tile


def test_member_tile_for_an_id_nobody_holds_a_seat_under_is_404(client, conn):
    add_members(conn)

    response = client.get(f"/tiles/members/{PAT}")

    assert response.status_code == 404
    assert "Nobody holding a seat in Congress has that ID." in response.text


def test_member_tile_refuses_a_malformed_id(client):
    assert client.get("/tiles/members/not-an-id").status_code == 422


def test_member_tile_with_no_votes_says_so(client, conn):
    add_pat(conn)

    tile = client.get(f"/tiles/members/{PAT}").text

    assert "No roll calls are on record for this member yet." in tile
    assert "No votes to show." in tile
    assert "Show more votes" not in tile


def add_votes(conn, how_many):
    """Store that many final votes for Pat, one a day, roll 1 the oldest."""
    for number in range(1, how_many + 1):
        title = f"Act {number}"
        add_vote(conn, number, day=number, positions={PAT: "Yea"}, title=title)


def test_first_page_holds_twenty_votes_and_offers_more(client, conn):
    add_pat(conn)
    add_votes(conn, 25)

    tile = client.get(f"/tiles/members/{PAT}").text

    assert tile.count('class="vote"') == 20
    assert "Act 25<" in tile
    assert "Act 6<" in tile
    assert "Act 5<" not in tile
    assert f"/tiles/members/{PAT}/votes?topic=&amp;offset=20" in tile


def test_next_page_holds_the_rest_and_offers_no_more(client, conn):
    add_pat(conn)
    add_votes(conn, 25)

    rows = client.get(f"/tiles/members/{PAT}/votes", params={"offset": 20}).text

    assert rows.count('class="vote"') == 5
    assert "Act 5<" in rows
    assert "Act 1<" in rows
    assert "Act 6<" not in rows
    assert "Show more votes" not in rows


def test_exactly_twenty_votes_offers_no_more(client, conn):
    add_pat(conn)
    add_votes(conn, 20)

    tile = client.get(f"/tiles/members/{PAT}").text

    assert tile.count('class="vote"') == 20
    assert "Show more votes" not in tile


def test_vote_rows_for_a_topic_hold_only_that_topic(client, conn):
    add_pat(conn)
    add_bill(conn, 9999, policy_area="Health")
    add_bill(conn, 8888, policy_area="Energy")
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 9999", title="A")
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, issue="H R 8888", title="B")

    rows = client.get(f"/tiles/members/{PAT}/votes", params={"topic": "Health"}).text

    assert rows.count('class="vote"') == 1


def test_more_votes_for_a_topic_keep_the_topic(client, conn):
    add_pat(conn)
    add_bill(conn, 9999, policy_area="Crime and Law Enforcement")
    add_votes(conn, 21)

    rows = client.get(
        f"/tiles/members/{PAT}/votes", params={"topic": "Crime and Law Enforcement"}
    ).text

    assert "topic=Crime%20and%20Law%20Enforcement&amp;offset=20" in rows


def test_an_empty_topic_means_all_topics(client, conn):
    add_pat(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})

    rows = client.get(f"/tiles/members/{PAT}/votes", params={"topic": ""}).text

    assert rows.count('class="vote"') == 1


def test_vote_rows_refuse_a_negative_offset(client):
    response = client.get(f"/tiles/members/{PAT}/votes", params={"offset": -1})

    assert response.status_code == 422
