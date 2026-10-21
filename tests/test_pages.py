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
    add_generated,
    add_members,
    add_vote,
    official,
    summary,
)

from app.bills import bill_label
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


# What a response carries to empty a tile out of band.
CLEARS_OFFICIALS = '<section id="officials-tile" hx-swap-oob="innerHTML"></section>'
CLEARS_MEMBER = '<section id="member-tile" hx-swap-oob="innerHTML"></section>'
CLEARS_BILL = '<section id="bill-tile" hx-swap-oob="innerHTML"></section>'


def test_clear_button_empties_the_fields_and_asks_for_empty_tiles(client):
    page = client.get("/").text

    assert (
        '<button type="reset" hx-get="/tiles/clear" hx-swap="none">Clear</button>'
        in page
    )


def test_clear_returns_every_tile_below_the_address_empty_and_nothing_else(client):
    response = client.get("/tiles/clear")

    assert response.status_code == 200
    assert CLEARS_OFFICIALS in response.text
    assert CLEARS_MEMBER in response.text
    assert CLEARS_BILL in response.text
    rest = response.text
    for section in (CLEARS_OFFICIALS, CLEARS_MEMBER, CLEARS_BILL):
        rest = rest.replace(section, "")
    assert rest.strip() == ""


def test_officials_tile_empties_the_member_and_bill_tiles(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A))
    geocode_to(monkeypatch, CAPITOL)

    tile = client.post("/tiles/officials", data=ADDRESS).text

    assert CLEARS_MEMBER in tile
    assert CLEARS_BILL in tile


def test_every_message_in_the_officials_tile_empties_the_tiles_below(
    client, monkeypatch
):
    def unavailable(address):
        raise GeocoderUnavailable("ConnectTimeout")

    responses = [client.post("/tiles/officials", data={})]
    geocode_to(monkeypatch, None)
    responses.append(client.post("/tiles/officials", data=ADDRESS))
    geocode_to(monkeypatch, MID_ATLANTIC)
    responses.append(client.post("/tiles/officials", data=ADDRESS))
    monkeypatch.setattr("app.pages.geocode", unavailable)
    responses.append(client.post("/tiles/officials", data=ADDRESS))

    assert [r.status_code for r in responses] == [422, 404, 404, 503]
    for response in responses:
        assert CLEARS_MEMBER in response.text
        assert CLEARS_BILL in response.text


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
    # The message goes into the member tile, so it must not empty that tile.
    assert "hx-swap-oob" not in response.text


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


# The bill tile. add_bill stores H.R. 9999 of the 119th Congress, sponsored
# by Pat Example, which is the bill the made-up votes name by default.
BILL = "/tiles/bills/119/hr/9999"


def test_a_vote_on_a_stored_bill_opens_the_bill_tile(client, conn):
    add_pat(conn)
    add_bill(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})

    tile = client.get(f"/tiles/members/{PAT}").text

    assert f'hx-get="{BILL}"' in tile
    assert 'hx-target="#bill-tile"' in tile


def test_a_vote_whose_bill_is_not_stored_opens_nothing(client, conn):
    add_pat(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 7777", title="New Act")

    tile = client.get(f"/tiles/members/{PAT}").text

    assert "New Act" in tile
    assert "/tiles/bills/" not in tile


def test_opening_a_member_empties_the_bill_tile(client, conn):
    add_pat(conn)

    tile = client.get(f"/tiles/members/{PAT}").text

    assert CLEARS_BILL in tile
    assert CLEARS_MEMBER not in tile


def test_bill_tile_shows_the_bill_its_sponsor_and_where_it_stands(client, conn):
    add_members(conn)
    add_bill(conn)

    response = client.get(BILL)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "H.R. 9999" in response.text
    assert "Example Act" in response.text
    assert "Pat Example" in response.text
    assert "Jan 15, 2025" in response.text
    assert "May 9, 2025" in response.text
    assert "Became Public Law." in response.text
    assert "Energy" in response.text
    assert 'href="https://example.test/bill/9999"' in response.text


def test_bill_tile_shows_summaries_newest_stage_first_with_only_the_newest_open(
    client, conn
):
    add_members(conn)
    add_bill(
        conn,
        summaries=[
            summary("00", date(2025, 1, 15), "Introduced in House", "First words."),
            summary("49", date(2025, 5, 9), "Public Law", "Last words."),
        ],
    )

    tile = client.get(BILL).text

    assert tile.index("Last words.") < tile.index("First words.")
    assert "Introduced in House, Jan 15, 2025" in tile
    assert tile.count('<details class="summary" open>') == 1
    assert tile.count('<details class="summary">') == 1


def test_bill_tile_says_when_there_is_no_official_summary(client, conn):
    add_members(conn)
    add_bill(conn)

    tile = client.get(BILL).text

    assert "No official summary has been published for this bill yet." in tile
    assert "<details" not in tile


def test_a_summary_is_shown_as_text_and_never_as_html(client, conn):
    add_members(conn)
    text = "<script>alert(1)</script>"
    add_bill(conn, summaries=[summary("00", date(2025, 1, 15), "Introduced", text)])

    tile = client.get(BILL).text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in tile
    assert "<script>" not in tile


def test_bill_tile_never_sends_a_summary_s_original_html(client, conn):
    add_members(conn)
    text = "Plain words."
    add_bill(conn, summaries=[summary("00", date(2025, 1, 15), "Introduced", text)])

    tile = client.get(BILL).text

    assert "Plain words." in tile
    # The made-up original HTML wraps the text in <p> and </p>.
    assert "<p>Plain words.</p>" not in tile


def test_bill_tile_never_links_to_something_that_is_not_a_web_address(client, conn):
    add_members(conn)
    add_bill(conn)
    conn.execute("UPDATE bills SET source_url = 'javascript:alert(1)'")

    tile = client.get(BILL).text

    assert "javascript:" not in tile


INTRODUCED = summary("00", date(2025, 1, 15), "Introduced in House", "CRS words.")
POLICY = "https://github.com/jrwiegsDev/do-politics/blob/main/docs/editorial-policy.md"


def test_bill_tile_shows_an_approved_summary_above_the_official_ones(client, conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Plain words")

    tile = client.get(BILL).text

    assert "In plain language" in tile
    assert "How this does you" in tile
    assert "How Plain words does you." in tile
    assert tile.index("Plain words") < tile.index("Official summaries")
    assert tile.index("Official summaries") < tile.index("CRS words.")


def test_an_approved_summary_is_labelled_as_generated_with_its_source(client, conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Plain words")

    tile = " ".join(client.get(BILL).text.split())

    assert "Written by Claude, an AI model, from the official summary below" in tile
    assert "(Introduced in House, Jan 15, 2025)" in tile
    assert "reviewed by DoPolitics" in tile
    assert f'href="{POLICY}"' in tile


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_bill_tile_never_shows_a_summary_nobody_approved(client, conn, status):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Unreviewed words", status)

    tile = client.get(BILL).text

    assert "Unreviewed words" not in tile
    assert "In plain language" not in tile
    assert "CRS words." in tile


def test_a_generated_summary_is_shown_as_text_and_never_as_html(client, conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "<script>alert(1)</script>")

    tile = client.get(BILL).text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in tile
    assert "<script>" not in tile


def test_bill_tile_for_a_bill_not_stored_is_404(client, conn):
    response = client.get("/tiles/bills/119/hr/1")

    assert response.status_code == 404
    assert "That bill is not in our records yet." in response.text


@pytest.mark.parametrize(
    "path",
    ["/tiles/bills/119/xyz/1", "/tiles/bills/0/hr/1", "/tiles/bills/119/hr/0"],
)
def test_bill_tile_refuses_a_malformed_bill(client, path):
    assert client.get(path).status_code == 422


@pytest.mark.parametrize(
    ("bill_type", "bill_number", "label"),
    [
        ("hr", 1, "H.R. 1"),
        ("s", 5, "S. 5"),
        ("hjres", 12, "H.J.Res. 12"),
        ("sconres", 7, "S.Con.Res. 7"),
        ("sres", 300, "S.Res. 300"),
    ],
)
def test_a_bill_is_named_the_way_congress_writes_it(bill_type, bill_number, label):
    assert bill_label(bill_type, bill_number) == label
