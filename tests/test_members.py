"""Tests for the members loader."""

import psycopg
import pytest

from ingest.members import UPSERT_SQL, read_members, to_row

SENATOR = {
    "id": {"bioguide": "T000003", "lis": "S900"},
    "name": {"first": "Sam", "last": "Sample", "official_full": "Sam R. Sample"},
    "terms": [
        {"type": "rep", "state": "IL", "district": 13, "start": "2019-01-03", "end": "2021-01-03"},
        {"type": "sen", "state": "IL", "start": "2021-01-03", "end": "2027-01-03"},
    ],
}

FORMER_MEMBER = {
    "id": {"bioguide": "T000004"},
    "name": {"first": "Pat", "last": "Example"},
    "terms": [
        {"type": "rep", "state": "IL", "district": 12, "start": "2023-01-03", "end": "2025-01-03"},
    ],
}

FILES = {
    "legislators-current": [SENATOR],
    "legislators-historical": [FORMER_MEMBER],
}


def test_senator_becomes_a_row_with_an_lis_id():
    row = to_row(SENATOR, "legislators-current")

    assert row == {
        "bioguide_id": "T000003",
        "lis_id": "S900",
        "name": "Sam R. Sample",
        "source": "legislators-current",
    }


def test_house_only_member_has_no_lis_id():
    row = to_row(FORMER_MEMBER, "legislators-historical")

    assert row["lis_id"] is None


def test_name_falls_back_to_first_and_last():
    row = to_row(FORMER_MEMBER, "legislators-historical")

    assert row["name"] == "Pat Example"


def test_upserting_a_member_twice_keeps_one_row(conn):
    conn.execute(UPSERT_SQL, to_row(SENATOR, "legislators-current"))
    conn.execute(UPSERT_SQL, to_row(SENATOR, "legislators-current"))

    count = conn.execute("SELECT count(*) FROM members").fetchone()[0]

    assert count == 1


def test_member_who_left_office_moves_to_the_historical_source(conn):
    conn.execute(UPSERT_SQL, to_row(SENATOR, "legislators-current"))
    conn.execute(UPSERT_SQL, to_row(SENATOR, "legislators-historical"))

    source = conn.execute("SELECT source FROM members WHERE bioguide_id = 'T000003'").fetchone()[0]

    assert source == "legislators-historical"


def test_two_members_cannot_share_an_lis_id(conn):
    conn.execute(UPSERT_SQL, to_row(SENATOR, "legislators-current"))
    impostor = to_row(SENATOR, "legislators-current") | {"bioguide_id": "T000005"}

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(UPSERT_SQL, impostor)


def test_people_from_both_files_are_read_with_their_source(monkeypatch):
    monkeypatch.setattr("ingest.members.MIN_EXPECTED_MEMBERS", 1)
    monkeypatch.setattr("ingest.members.fetch", lambda source: FILES[source])

    assert read_members() == [
        to_row(SENATOR, "legislators-current"),
        to_row(FORMER_MEMBER, "legislators-historical"),
    ]


def test_too_few_members_is_refused(monkeypatch):
    monkeypatch.setattr("ingest.members.fetch", lambda source: FILES[source])

    with pytest.raises(ValueError):
        read_members()
