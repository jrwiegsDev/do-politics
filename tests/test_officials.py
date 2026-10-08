"""Tests for the officials adapter."""

from datetime import date

import pytest

from ingest.officials import DELETE_DEPARTED_SQL, UPSERT_SQL, district_code_for, read_officials, to_row

HOUSE_MEMBER = {
    "id": {"bioguide": "T000001"},
    "name": {"first": "Pat", "last": "Example", "official_full": "Pat Q. Example"},
    "terms": [
        {"type": "rep", "state": "IL", "district": 12, "party": "Republican", "start": "2023-01-03", "end": "2025-01-03"},
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

SENATOR = {
    "id": {"bioguide": "T000002"},
    "name": {"first": "Sam", "last": "Sample"},
    "terms": [
        {"type": "sen", "state": "IL", "party": "Independent", "start": "2023-01-03", "end": "2029-01-03"},
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
        "source": "legislators-current",
    }


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

    party = conn.execute("SELECT party FROM officials WHERE bioguide_id = 'T000001'").fetchone()[0]

    assert party == "Independent"


def test_departed_member_is_removed(conn):
    conn.execute(UPSERT_SQL, to_row(HOUSE_MEMBER))
    conn.execute(UPSERT_SQL, to_row(SENATOR))

    conn.execute(DELETE_DEPARTED_SQL, {"source": "legislators-current", "bioguide_ids": ["T000001"]})

    remaining = conn.execute("SELECT bioguide_id FROM officials").fetchall()

    assert remaining == [("T000001",)]


def test_a_file_with_too_few_members_is_refused(monkeypatch):
    monkeypatch.setattr("ingest.officials.fetch", lambda: [HOUSE_MEMBER, SENATOR])

    with pytest.raises(ValueError):
        read_officials()
