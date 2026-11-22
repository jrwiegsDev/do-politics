"""Tests for the officials adapter."""

from datetime import date
from typing import Any

import psycopg
import pytest
from helpers import last_run

from ingest.officials import (
    DELETE_DEPARTED_SQL,
    UPSERT_SQL,
    current_leadership_titles,
    district_code_for,
    load,
    read_officials,
    to_row,
    to_terms,
)

HOUSE_MEMBER: dict[str, Any] = {
    "id": {"bioguide": "T000001"},
    "name": {"first": "Pat", "last": "Example", "official_full": "Pat Q. Example"},
    "terms": [
        {
            "type": "rep",
            "state": "IL",
            "district": 12,
            "party": "Republican",
            "start": "2023-01-03",
            "end": "2025-01-03",
        },
        {
            "type": "rep",
            "state": "IL",
            "district": 13,
            "party": "Democrat",
            "start": "2025-01-03",
            "end": "2027-01-03",
            "phone": "202-555-0113",
            "address": "100 Example House Office Building Washington DC 20515",
            "url": "https://example.house.gov",
        },
    ],
}

SENATOR: dict[str, Any] = {
    "id": {"bioguide": "T000002"},
    "name": {"first": "Sam", "last": "Sample"},
    "terms": [
        {
            "type": "sen",
            "state": "IL",
            "party": "Independent",
            "start": "2023-01-03",
            "end": "2029-01-03",
        },
    ],
}


def test_numbered_district_is_zero_padded():
    assert district_code_for("IL", 3) == "03"


def test_at_large_state_uses_00():
    assert district_code_for("WY", 0) == "00"


def test_non_voting_seat_uses_98():
    assert district_code_for("DC", 0) == "98"


def test_house_member_becomes_a_row_from_the_latest_term():
    row = to_row(HOUSE_MEMBER)

    assert row == {
        "bioguide_id": "T000001",
        "name": "Pat Q. Example",
        "party": "Democrat",
        "chamber": "house",
        "state": "IL",
        "state_fips": "17",
        "district_code": "13",
        "term_start": date(2025, 1, 3),
        "term_end": date(2027, 1, 3),
        "phone": "202-555-0113",
        "office_address": "100 Example House Office Building Washington DC 20515",
        "website": "https://example.house.gov",
        "contact_form": None,
        "leadership_titles": [],
        "source": "legislators-current",
    }


ON_DATE = date(2026, 10, 21)


def with_roles(*roles):
    """Give the made-up senator these leadership roles."""
    return SENATOR | {"leadership_roles": list(roles)}


def test_a_role_still_held_is_current():
    member = with_roles({"title": "Senate Minority Whip", "start": "2025-01-03"})

    assert current_leadership_titles(member, ON_DATE) == ["Senate Minority Whip"]


def test_a_role_that_has_ended_or_not_started_is_left_out():
    member = with_roles(
        {"title": "Senate Majority Leader", "start": "2019-01-03", "end": "2021-01-03"},
        {"title": "Speaker of the House", "start": "2027-01-03"},
    )

    assert current_leadership_titles(member, ON_DATE) == []


def test_a_role_ends_on_its_end_date():
    member = with_roles(
        {"title": "Senate Majority Leader", "start": "2025-01-03", "end": "2026-10-21"}
    )

    assert current_leadership_titles(member, date(2026, 10, 20)) == [
        "Senate Majority Leader"
    ]
    assert current_leadership_titles(member, ON_DATE) == []


def test_every_current_role_is_kept():
    member = with_roles(
        {"title": "Senate Minority Leader", "start": "2025-01-03"},
        {"title": "Senate Democratic Conference Chair", "start": "2025-01-03"},
    )

    assert current_leadership_titles(member, ON_DATE) == [
        "Senate Minority Leader",
        "Senate Democratic Conference Chair",
    ]


def test_a_member_with_no_roles_has_none():
    assert current_leadership_titles(SENATOR, ON_DATE) == []


def test_the_row_carries_the_current_roles():
    member = with_roles({"title": "Senate Minority Whip", "start": "2025-01-03"})

    assert to_row(member, ON_DATE)["leadership_titles"] == ["Senate Minority Whip"]


def test_senator_has_no_district():
    row = to_row(SENATOR)

    assert row["chamber"] == "senate"
    assert row["district_code"] is None


def test_name_falls_back_to_first_and_last():
    row = to_row(SENATOR)

    assert row["name"] == "Sam Sample"


def test_upserting_a_member_twice_keeps_one_row(conn):
    conn.execute(UPSERT_SQL, to_row(HOUSE_MEMBER))
    conn.execute(UPSERT_SQL, to_row(HOUSE_MEMBER))

    count = conn.execute("SELECT count(*) FROM officials").fetchone()[0]

    assert count == 1


def test_upsert_updates_a_member_who_changed(conn):
    conn.execute(UPSERT_SQL, to_row(HOUSE_MEMBER))
    changed = to_row(HOUSE_MEMBER) | {"party": "Independent"}
    conn.execute(UPSERT_SQL, changed)

    party = conn.execute(
        "SELECT party FROM officials WHERE bioguide_id = 'T000001'"
    ).fetchone()[0]

    assert party == "Independent"


def test_departed_member_is_removed(conn):
    conn.execute(UPSERT_SQL, to_row(HOUSE_MEMBER))
    conn.execute(UPSERT_SQL, to_row(SENATOR))

    conn.execute(
        DELETE_DEPARTED_SQL,
        {"source": "legislators-current", "bioguide_ids": ["T000001"]},
    )

    remaining = conn.execute("SELECT bioguide_id FROM officials").fetchall()

    assert remaining == [("T000001",)]


def test_a_file_with_too_few_members_is_refused(monkeypatch):
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, SENATOR])

    with pytest.raises(ValueError):
        read_officials()


def test_load_adds_the_members_removes_the_departed_and_records_the_run(
    committing_conn, monkeypatch
):
    departed = to_row(HOUSE_MEMBER | {"id": {"bioguide": "T000009"}})
    committing_conn.execute(UPSERT_SQL, departed)
    monkeypatch.setattr("ingest.officials.MIN_EXPECTED_MEMBERS", 1)
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, SENATOR])

    loaded, terms, removed = load(committing_conn)

    in_office = committing_conn.execute(
        "SELECT bioguide_id FROM officials ORDER BY bioguide_id"
    ).fetchall()
    recorded = last_run(committing_conn)
    assert (loaded, terms, removed) == (2, 3, 1)
    assert in_office == [("T000001",), ("T000002",)]
    assert (recorded["job"], recorded["status"]) == ("officials", "succeeded")
    assert recorded["rows_loaded"] == 2


def test_load_of_a_short_file_fails_the_run_and_removes_nobody(
    committing_conn, monkeypatch
):
    committing_conn.execute(UPSERT_SQL, to_row(SENATOR))
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER])

    with pytest.raises(ValueError):
        load(committing_conn)

    in_office = committing_conn.execute("SELECT bioguide_id FROM officials").fetchall()
    recorded = last_run(committing_conn)
    assert in_office == [("T000002",)]
    assert (recorded["job"], recorded["status"]) == ("officials", "failed")
    assert recorded["error"].startswith("ValueError: legislators-current has only 1")


def test_every_term_becomes_a_row_oldest_first():
    rows = to_terms(HOUSE_MEMBER)

    assert [(r["position"], r["district"], r["party"]) for r in rows] == [
        (0, 12, "Republican"),
        (1, 13, "Democrat"),
    ]
    assert rows[0]["chamber"] == "house"
    assert rows[0]["starts_on"] == date(2023, 1, 3)
    assert rows[1]["ends_on"] == date(2027, 1, 3)


def test_a_senate_term_has_no_district_and_keeps_how_it_began():
    appointed = SENATOR | {
        "terms": [SENATOR["terms"][0] | {"how": "appointment"}],
    }

    (row,) = to_terms(appointed)

    assert (row["chamber"], row["district"], row["how"]) == (
        "senate",
        None,
        "appointment",
    )


def terms_in_db(conn):
    return conn.execute(
        "SELECT bioguide_id, position, chamber FROM official_terms"
        " ORDER BY bioguide_id, position"
    ).fetchall()


def test_load_stores_every_term_and_replaces_them_on_the_next_run(
    committing_conn, monkeypatch
):
    monkeypatch.setattr("ingest.officials.MIN_EXPECTED_MEMBERS", 1)
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, SENATOR])
    load(committing_conn)
    assert terms_in_db(committing_conn) == [
        ("T000001", 0, "house"),
        ("T000001", 1, "house"),
        ("T000002", 0, "senate"),
    ]

    # The next file lists only the representative's current term.
    trimmed = HOUSE_MEMBER | {"terms": HOUSE_MEMBER["terms"][1:]}
    monkeypatch.setattr("ingest.officials.fetch", lambda: [trimmed, SENATOR])
    load(committing_conn)

    assert terms_in_db(committing_conn) == [
        ("T000001", 0, "house"),
        ("T000002", 0, "senate"),
    ]


def test_a_departed_members_terms_go_with_them(committing_conn, monkeypatch):
    monkeypatch.setattr("ingest.officials.MIN_EXPECTED_MEMBERS", 1)
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, SENATOR])
    load(committing_conn)

    monkeypatch.setattr("ingest.officials.fetch", lambda: [SENATOR])
    load(committing_conn)

    assert terms_in_db(committing_conn) == [("T000002", 0, "senate")]


def test_a_failed_run_leaves_the_terms_as_they_were(committing_conn, monkeypatch):
    monkeypatch.setattr("ingest.officials.MIN_EXPECTED_MEMBERS", 1)
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, SENATOR])
    load(committing_conn)
    broken = SENATOR | {"terms": [SENATOR["terms"][0] | {"end": "2020-01-01"}]}
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, broken])

    # official_terms_dates_check refuses a term that ends before it starts.
    with pytest.raises(psycopg.errors.CheckViolation):
        load(committing_conn)

    assert len(terms_in_db(committing_conn)) == 3
