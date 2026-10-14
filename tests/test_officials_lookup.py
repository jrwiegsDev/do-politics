"""Tests for the officials lookup."""

from datetime import date

from helpers import MISSOURI_SENATOR, REP_12, REP_13, SENATOR_A, SENATOR_B, add, names

from app.officials import find_official, find_officials

ON_DATE = date(2026, 6, 1)


def test_senators_come_first_then_the_representative(conn):
    add(conn, REP_13, SENATOR_B, SENATOR_A)

    found = find_officials(conn, "17", "13", on_date=ON_DATE)

    assert names(found) == ["Alex Senator", "Blake Senator", "Casey Rep"]


def test_other_districts_and_states_are_left_out(conn):
    add(conn, SENATOR_A, REP_13, REP_12, MISSOURI_SENATOR)

    found = find_officials(conn, "17", "13", on_date=ON_DATE)

    assert names(found) == ["Alex Senator", "Casey Rep"]


def test_vacant_seat_returns_only_the_senators(conn):
    add(conn, SENATOR_A, SENATOR_B, REP_12)

    found = find_officials(conn, "17", "13", on_date=ON_DATE)

    assert names(found) == ["Alex Senator", "Blake Senator"]


def test_member_is_left_out_from_the_day_the_term_ends(conn):
    add(conn, REP_13)

    last_day = find_officials(conn, "17", "13", on_date=date(2027, 1, 2))
    end_day = find_officials(conn, "17", "13", on_date=date(2027, 1, 3))

    assert names(last_day) == ["Casey Rep"]
    assert names(end_day) == []


def test_one_official_is_found_by_bioguide_id(conn):
    add(conn, SENATOR_A, REP_13)

    found = find_official(conn, "T000012")

    assert found is not None
    assert (found["name"], found["chamber"], found["district_code"]) == (
        "Casey Rep",
        "house",
        "13",
    )


def test_an_id_nobody_holds_a_seat_under_finds_nothing(conn):
    add(conn, SENATOR_A)

    assert find_official(conn, "T000012") is None
