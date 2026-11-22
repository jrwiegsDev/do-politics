"""Tests for the daily usage counts: what is counted, by which day, and that a
count can never change what a visitor gets back. The geocoder is replaced, so
no test calls the Census."""

import logging
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from helpers import REP_13, SENATOR_A, SENATOR_B, add, add_bill, add_members

from app.geocoder import GeocoderUnavailable
from app.main import app, get_conn
from app.ratelimit import LOOKUP_LIMIT
from app.usage import count_bill_view, count_lookup, count_search, today
from ingest.states import FIPS_BY_POSTAL

CAPITOL = (-89.6548, 39.7984)
MID_ATLANTIC = (-40.0, 35.0)
ADDRESS = {
    "street": "401 S 2nd St",
    "city": "Springfield",
    "state": "IL",
    "zip_code": "62701",
}
BILL = "/tiles/bills/119/hr/9999"
NOV_22_NOON = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
NOV_23_NOON = datetime(2026, 11, 23, 18, 0, tzinfo=UTC)


def seated(row):
    """Give a made-up official a term that is still running whenever the tests run."""
    return row | {"term_end": date(2099, 1, 3)}


@pytest.fixture
def client(conn):
    """A client whose requests use the test database."""
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def lookups(conn):
    rows = conn.execute(
        "SELECT day, outcome, state_fips, lookups FROM lookup_counts"
        " ORDER BY day, outcome, state_fips"
    )
    return rows.fetchall()


def views(conn):
    rows = conn.execute(
        "SELECT b.bill_number, v.opened_from, v.views"
        " FROM bill_view_counts v JOIN bills b ON b.id = v.bill_id"
        " ORDER BY b.bill_number, v.opened_from"
    )
    return rows.fetchall()


def searches(conn):
    rows = conn.execute(
        "SELECT day, requests, empty_requests FROM search_counts ORDER BY day"
    )
    return rows.fetchall()


# The day boundary


@pytest.mark.parametrize(
    ("moment", "day"),
    [
        # Central Standard Time is UTC-6: midnight in Illinois is 06:00 UTC.
        (datetime(2026, 11, 23, 5, 59, tzinfo=UTC), date(2026, 11, 22)),
        (datetime(2026, 11, 23, 6, 0, tzinfo=UTC), date(2026, 11, 23)),
        # Central Daylight Time is UTC-5: midnight is 05:00 UTC.
        (datetime(2026, 7, 4, 4, 59, tzinfo=UTC), date(2026, 7, 3)),
        (datetime(2026, 7, 4, 5, 0, tzinfo=UTC), date(2026, 7, 4)),
    ],
)
def test_a_day_ends_at_midnight_central_time(moment, day):
    assert today(moment) == day


# Adding up


def test_lookups_add_up_in_one_row_per_day_outcome_and_state(conn):
    count_lookup(conn, "found", "17", now=NOV_22_NOON)
    count_lookup(conn, "found", "17", now=NOV_22_NOON)
    count_lookup(conn, "found", "06", now=NOV_22_NOON)
    count_lookup(conn, "not_found", now=NOV_22_NOON)
    count_lookup(conn, "not_found", now=NOV_22_NOON)
    count_lookup(conn, "found", "17", now=NOV_23_NOON)

    assert lookups(conn) == [
        (date(2026, 11, 22), "found", "06", 1),
        (date(2026, 11, 22), "found", "17", 2),
        (date(2026, 11, 22), "not_found", None, 2),
        (date(2026, 11, 23), "found", "17", 1),
    ]


def test_bill_views_add_up_by_where_the_bill_was_opened_from(conn):
    add_members(conn)
    add_bill(conn, 9999)
    add_bill(conn, 8888)

    count_bill_view(conn, 119, "hr", 9999, "search", now=NOV_22_NOON)
    count_bill_view(conn, 119, "hr", 9999, "search", now=NOV_22_NOON)
    count_bill_view(conn, 119, "hr", 9999, "votes", now=NOV_22_NOON)
    count_bill_view(conn, 119, "hr", 8888, "votes", now=NOV_22_NOON)

    assert views(conn) == [(8888, "votes", 1), (9999, "search", 2), (9999, "votes", 1)]


def test_a_view_of_a_bill_not_stored_counts_nothing(conn):
    count_bill_view(conn, 119, "hr", 4242, "search", now=NOV_22_NOON)

    assert views(conn) == []


def test_searches_count_requests_and_the_ones_that_found_nothing(conn):
    count_search(conn, found_nothing=False, now=NOV_22_NOON)
    count_search(conn, found_nothing=True, now=NOV_22_NOON)
    count_search(conn, found_nothing=False, now=NOV_22_NOON)
    count_search(conn, found_nothing=True, now=NOV_23_NOON)

    assert searches(conn) == [(date(2026, 11, 22), 3, 1), (date(2026, 11, 23), 1, 1)]


# What the database refuses


@pytest.mark.parametrize(
    ("outcome", "state_fips"),
    [
        ("not_found", "17"),  # a failed lookup never learns a state
        ("found", None),  # a found one always has one
        ("found", "99"),  # not a state
        ("notfound", None),  # not an outcome
    ],
)
def test_the_database_refuses_a_count_that_breaks_its_rules(
    conn, caplog, outcome, state_fips
):
    with caplog.at_level(logging.ERROR, logger="app.usage"):
        count_lookup(conn, outcome, state_fips, now=NOV_22_NOON)

    assert lookups(conn) == []
    assert "A usage count could not be saved" in caplog.text


def test_a_failed_count_leaves_the_rest_of_the_transaction_usable(conn):
    conn.execute("SELECT 1")  # the request's own work has opened a transaction

    count_lookup(conn, "not_found", "17", now=NOV_22_NOON)

    assert conn.execute("SELECT 2").fetchone() == (2,)
    count_lookup(conn, "found", "17", now=NOV_22_NOON)
    assert lookups(conn) == [(date(2026, 11, 22), "found", "17", 1)]


# The states table


def test_states_holds_everything_with_a_seat_in_congress(conn):
    rows = conn.execute("SELECT postal, fips FROM states").fetchall()

    assert dict(rows) == FIPS_BY_POSTAL


def test_every_state_rolls_up_to_a_census_region(conn):
    rows = conn.execute(
        "SELECT region, count(*) FROM states GROUP BY region ORDER BY region"
    )

    assert rows.fetchall() == [
        ("Midwest", 12),
        ("Northeast", 9),
        ("South", 17),
        ("Territories", 5),
        ("West", 13),
    ]


# The privacy promise, kept by the schema


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("lookup_counts", ["day", "outcome", "state_fips", "lookups"]),
        ("bill_view_counts", ["day", "bill_id", "opened_from", "views"]),
        ("search_counts", ["day", "requests", "empty_requests"]),
    ],
)
def test_count_tables_have_no_column_beyond_the_privacy_policy(conn, table, columns):
    # A new column here means the privacy policy (/privacy) must say so too.
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = %s ORDER BY ordinal_position",
        [table],
    )

    assert [name for (name,) in rows] == columns


def test_counts_keep_a_date_never_a_time_of_day(conn):
    rows = conn.execute(
        "SELECT DISTINCT data_type FROM information_schema.columns"
        " WHERE table_name IN ('lookup_counts', 'bill_view_counts', 'search_counts')"
        " AND column_name = 'day'"
    )

    assert rows.fetchall() == [("date",)]


# The lookup page and the JSON lookup


def test_a_lookup_that_finds_officials_counts_its_state_and_nothing_more(
    client, conn, monkeypatch
):
    add(conn, seated(SENATOR_A), seated(SENATOR_B), seated(REP_13))
    monkeypatch.setattr("app.pages.geocode", lambda address: CAPITOL)

    client.post("/tiles/officials", data=ADDRESS)

    assert lookups(conn) == [(today(), "found", "17", 1)]


@pytest.mark.parametrize(
    ("geocode", "form", "outcome"),
    [
        (lambda address: None, ADDRESS, "not_found"),
        (lambda address: MID_ATLANTIC, ADDRESS, "no_district"),
        (lambda address: CAPITOL, {"street": "401 S 2nd St"}, "invalid"),
    ],
)
def test_a_lookup_that_fails_counts_its_outcome_with_no_state(
    client, conn, monkeypatch, geocode, form, outcome
):
    monkeypatch.setattr("app.pages.geocode", geocode)

    client.post("/tiles/officials", data=form)

    assert lookups(conn) == [(today(), outcome, None, 1)]


def test_a_lookup_when_the_census_is_down_counts_as_unavailable(
    client, conn, monkeypatch
):
    def down(address):
        raise GeocoderUnavailable

    monkeypatch.setattr("app.pages.geocode", down)

    client.post("/tiles/officials", data=ADDRESS)

    assert lookups(conn) == [(today(), "unavailable", None, 1)]


def test_lookups_past_the_limit_count_as_rate_limited(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A))
    monkeypatch.setattr("app.pages.geocode", lambda address: CAPITOL)

    for _ in range(LOOKUP_LIMIT + 2):
        client.post("/tiles/officials", data=ADDRESS)

    assert lookups(conn) == [
        (today(), "found", "17", LOOKUP_LIMIT),
        (today(), "rate_limited", None, 2),
    ]


def test_the_json_lookup_is_counted_the_same_way(client, conn, monkeypatch):
    add(conn, seated(SENATOR_A))
    monkeypatch.setattr("app.main.geocode", lambda address: CAPITOL)
    client.post("/lookup", json=ADDRESS)
    monkeypatch.setattr("app.main.geocode", lambda address: None)
    client.post("/lookup", json=ADDRESS)

    assert lookups(conn) == [
        (today(), "found", "17", 1),
        (today(), "not_found", None, 1),
    ]


def test_a_json_lookup_outside_every_district_is_counted_through_the_real_pool(
    conn, monkeypatch
):
    # No stand-in connection here: the app's own pool rolls back a request
    # that raises, so this proves /lookup saves the count before its 404.
    monkeypatch.setattr("app.main.geocode", lambda address: MID_ATLANTIC)

    with TestClient(app) as client:
        response = client.post("/lookup", json=ADDRESS)

    assert response.status_code == 404
    assert lookups(conn) == [(today(), "no_district", None, 1)]


def test_a_count_that_fails_still_gives_the_visitor_their_officials(
    client, conn, monkeypatch, caplog
):
    add(conn, seated(SENATOR_A), seated(SENATOR_B), seated(REP_13))
    monkeypatch.setattr("app.pages.geocode", lambda address: CAPITOL)
    monkeypatch.setattr(
        "app.usage.COUNT_LOOKUP_SQL", "INSERT INTO no_such_table VALUES (1)"
    )

    with caplog.at_level(logging.ERROR, logger="app.usage"):
        response = client.post("/tiles/officials", data=ADDRESS)

    assert response.status_code == 200
    assert "Casey Rep" in response.text
    assert "A usage count could not be saved" in caplog.text


# Bill Search and the bill tile


def test_each_first_page_of_search_results_is_one_request(client, conn):
    add_members(conn)
    add_bill(conn, 9999, title="Veterans Benefits Act")

    for typed in ("vet", "veter", "veterans", "zzz"):
        client.get("/tiles/bills/search", params={"q": typed})

    assert searches(conn) == [(today(), 4, 1)]


def test_a_more_page_and_an_empty_box_are_not_counted(client, conn):
    add_members(conn)
    add_bill(conn, 9999, title="Veterans Benefits Act")

    client.get("/tiles/bills/search", params={"q": "veterans", "offset": 10})
    client.get("/tiles/bills/search", params={"q": "  "})

    assert searches(conn) == []


def test_a_bill_opened_from_search_and_from_a_members_votes_count_apart(client, conn):
    add_members(conn)
    add_bill(conn, 9999)

    client.get(BILL)
    client.get(BILL, params={"member": "T000001"})
    client.get(BILL, params={"member": "T000001"})

    assert views(conn) == [(9999, "search", 1), (9999, "votes", 2)]


def test_a_bill_not_in_our_records_is_not_counted(client, conn):
    response = client.get("/tiles/bills/119/hr/4242")

    assert response.status_code == 404
    assert views(conn) == []
