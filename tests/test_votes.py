"""Tests for the votes loader.

The XML below is made up, in each chamber's real layout, so no test
downloads anything and none depends on a real member staying in office.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import psycopg
import pytest

from helpers import MEMBERS, add_members
from ingest.votes import (
    EASTERN,
    check_belongs,
    house_url,
    house_votes,
    load,
    parse_house,
    parse_senate,
    recategorize,
    save,
    senate_menu_url,
    senate_url,
    senate_votes,
)

HOUSE_XML = """
<rollcall-vote>
  <vote-metadata>
    <congress>119</congress>
    <session>2nd</session>
    <rollcall-num>100</rollcall-num>
    <legis-num>H R 9999</legis-num>
    <vote-question>On Passage</vote-question>
    <vote-type>YEA-AND-NAY</vote-type>
    <vote-result>Passed</vote-result>
    <action-date>25-Mar-2026</action-date>
    <action-time time-etz="15:22">3:22 PM</action-time>
    <vote-desc>Example Act</vote-desc>
  </vote-metadata>
  <vote-data>
    <recorded-vote>
      <legislator name-id="T000001" party="D" state="IL">Example</legislator>
      <vote>Yea</vote>
    </recorded-vote>
    <recorded-vote>
      <legislator name-id="T000002" party="R" state="IL">Sample</legislator>
      <vote>Not Voting</vote>
    </recorded-vote>
  </vote-data>
</rollcall-vote>
"""

# What the Clerk sends, with HTTP 200, for a roll call that does not exist.
MISSING_XML = '<xml>Error sanitizing file "roll999.xml". Please try again.</xml>'

# The two spaces before the time are in the Senate's real files.
SENATE_XML = """
<roll_call_vote>
  <congress>119</congress>
  <session>2</session>
  <vote_number>7</vote_number>
  <vote_date>January 5, 2026,  05:31 PM</vote_date>
  <question>On the Nomination</question>
  <vote_title>Confirmation: Example Nominee</vote_title>
  <vote_result>Nomination Confirmed</vote_result>
  <document>
    <document_name>PN99</document_name>
  </document>
  <members>
    <member>
      <party>D</party>
      <state>IL</state>
      <vote_cast>Nay</vote_cast>
      <lis_member_id>S900</lis_member_id>
    </member>
    <member>
      <party>R</party>
      <state>IL</state>
      <vote_cast>Yea</vote_cast>
      <lis_member_id>S901</lis_member_id>
    </member>
  </members>
</roll_call_vote>
"""

SENATE_MENU_XML = """
<vote_summary>
  <votes>
    <vote><vote_number>00003</vote_number></vote>
    <vote><vote_number>00002</vote_number></vote>
    <vote><vote_number>00001</vote_number></vote>
  </votes>
</vote_summary>
"""

BIOGUIDE_BY_LIS = {"S900": "T000001", "S901": "T000002"}


def house(number: int = 100) -> ET.Element:
    """A made-up House roll call with the given number."""
    return ET.fromstring(HOUSE_XML.replace("<rollcall-num>100<", f"<rollcall-num>{number}<"))


def senate(number: int = 7) -> ET.Element:
    """A made-up Senate vote with the given number."""
    return ET.fromstring(SENATE_XML.replace("<vote_number>7<", f"<vote_number>{number}<"))


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def fake_fetch(documents: dict[str, ET.Element]):
    """Stand in for fetch_xml: known addresses get their document, the rest get the Clerk's error."""
    return lambda url: documents.get(url, ET.fromstring(MISSING_XML))


@pytest.fixture
def no_waiting(monkeypatch):
    """Skip the pause between requests, since nothing is really requested."""
    monkeypatch.setattr("ingest.votes.sleep", lambda seconds: None)


@pytest.fixture
def committing_conn(conn):
    """A connection for testing load(), which commits as it goes.

    The usual rollback cannot undo a commit, so this removes what the test
    left behind, and the next test still starts from the same data.
    """
    yield conn
    conn.rollback()
    conn.execute("DELETE FROM votes")
    conn.execute("DELETE FROM members WHERE bioguide_id = ANY(%s)", [[member["bioguide_id"] for member in MEMBERS]])
    conn.execute("DELETE FROM ingestion_runs WHERE job LIKE 'votes-%'")  # also matches votes-categories
    conn.commit()


def test_house_vote_becomes_a_row():
    vote, _ = parse_house(house(), "https://example.test/roll100.xml")

    assert vote == {
        "chamber": "house",
        "congress": 119,
        "session": 2,
        "roll_number": 100,
        "voted_at": datetime(2026, 3, 25, 15, 22, tzinfo=EASTERN),
        "question": "On Passage",
        "vote_type": "YEA-AND-NAY",
        "issue": "H R 9999",
        "title": "Example Act",
        "result": "Passed",
        "source_url": "https://example.test/roll100.xml",
    }


def test_house_time_during_daylight_saving_is_four_hours_behind_utc():
    vote, _ = parse_house(house(), "https://example.test/roll100.xml")

    assert vote["voted_at"] == datetime(2026, 3, 25, 19, 22, tzinfo=timezone.utc)


def test_house_positions_carry_the_id_party_and_state():
    _, positions = parse_house(house(), "https://example.test/roll100.xml")

    assert positions == [
        {"bioguide_id": "T000001", "position": "Yea", "party": "D", "state": "IL"},
        {"bioguide_id": "T000002", "position": "Not Voting", "party": "R", "state": "IL"},
    ]


def test_empty_house_description_becomes_none():
    root = ET.fromstring(HOUSE_XML.replace("<vote-desc>Example Act</vote-desc>", "<vote-desc></vote-desc>"))

    vote, _ = parse_house(root, "https://example.test/roll100.xml")

    assert vote["title"] is None


def test_senate_vote_becomes_a_row():
    vote, _ = parse_senate(senate(), "https://example.test/vote7.xml", BIOGUIDE_BY_LIS)

    assert vote == {
        "chamber": "senate",
        "congress": 119,
        "session": 2,
        "roll_number": 7,
        "voted_at": datetime(2026, 1, 5, 17, 31, tzinfo=EASTERN),
        "question": "On the Nomination",
        "vote_type": None,
        "issue": "PN99",
        "title": "Confirmation: Example Nominee",
        "result": "Nomination Confirmed",
        "source_url": "https://example.test/vote7.xml",
    }


def test_senate_time_during_standard_time_is_five_hours_behind_utc():
    vote, _ = parse_senate(senate(), "https://example.test/vote7.xml", BIOGUIDE_BY_LIS)

    assert vote["voted_at"] == datetime(2026, 1, 5, 22, 31, tzinfo=timezone.utc)


def test_senate_positions_are_translated_to_bioguide_ids():
    _, positions = parse_senate(senate(), "https://example.test/vote7.xml", BIOGUIDE_BY_LIS)

    assert positions == [
        {"bioguide_id": "T000001", "position": "Nay", "party": "D", "state": "IL"},
        {"bioguide_id": "T000002", "position": "Yea", "party": "R", "state": "IL"},
    ]


def test_senate_id_that_cannot_be_translated_is_refused():
    with pytest.raises(KeyError):
        parse_senate(senate(), "https://example.test/vote7.xml", {"S900": "T000001"})


def test_senate_vote_with_no_document_has_no_issue():
    root = ET.fromstring(SENATE_XML.replace("<document_name>PN99</document_name>", "<document_name/>"))

    vote, _ = parse_senate(root, "https://example.test/vote7.xml", BIOGUIDE_BY_LIS)

    assert vote["issue"] is None


def test_both_parsers_return_the_same_columns():
    house_vote, _ = parse_house(house(), "https://example.test/roll100.xml")
    senate_vote, _ = parse_senate(senate(), "https://example.test/vote7.xml", BIOGUIDE_BY_LIS)

    assert house_vote.keys() == senate_vote.keys()


def test_vote_from_another_session_is_refused():
    vote, _ = parse_house(house(), "https://example.test/roll100.xml")

    with pytest.raises(ValueError):
        check_belongs(vote, "house", 1)


def test_saving_a_vote_twice_keeps_one_vote_and_one_position_per_member(conn):
    add_members(conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")

    save(conn, vote, positions)
    save(conn, vote, positions)

    assert count(conn, "votes") == 1
    assert count(conn, "vote_positions") == 2


def test_saving_a_corrected_vote_updates_the_position(conn):
    add_members(conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")
    save(conn, vote, positions)

    corrected = [positions[0] | {"position": "Nay"}, positions[1]]
    save(conn, vote, corrected)

    position = conn.execute("SELECT position FROM vote_positions WHERE bioguide_id = 'T000001'").fetchone()[0]

    assert position == "Nay"


def test_position_for_an_unknown_member_is_refused(conn):
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        save(conn, vote, positions)


def test_deleting_a_vote_deletes_its_positions(conn):
    add_members(conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")
    save(conn, vote, positions)

    conn.execute("DELETE FROM votes")

    assert count(conn, "vote_positions") == 0


def test_member_with_a_vote_cannot_be_deleted(conn):
    add_members(conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")
    save(conn, vote, positions)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("DELETE FROM members WHERE bioguide_id = 'T000001'")


def test_house_votes_stop_at_the_first_missing_roll_call(monkeypatch, no_waiting):
    monkeypatch.setattr("ingest.votes.fetch_xml", fake_fetch({house_url(2, 1): house(1), house_url(2, 2): house(2)}))

    found = [vote["roll_number"] for vote, _ in house_votes(2, after=0)]

    assert found == [1, 2]


def test_house_votes_start_after_the_last_one_loaded(monkeypatch, no_waiting):
    monkeypatch.setattr("ingest.votes.fetch_xml", fake_fetch({house_url(2, 1): house(1), house_url(2, 2): house(2)}))

    found = [vote["roll_number"] for vote, _ in house_votes(2, after=1)]

    assert found == [2]


def test_senate_votes_come_oldest_first_and_skip_those_already_loaded(monkeypatch, no_waiting):
    documents = {senate_menu_url(2): ET.fromstring(SENATE_MENU_XML)}
    documents |= {senate_url(2, number): senate(number) for number in (1, 2, 3)}
    monkeypatch.setattr("ingest.votes.fetch_xml", fake_fetch(documents))

    found = [vote["roll_number"] for vote, _ in senate_votes(2, after=1, bioguide_by_lis=BIOGUIDE_BY_LIS)]

    assert found == [2, 3]


def test_load_saves_new_votes_and_a_second_run_finds_none(committing_conn, monkeypatch, no_waiting):
    add_members(committing_conn)
    monkeypatch.setattr("ingest.votes.fetch_xml", fake_fetch({house_url(2, 1): house(1), house_url(2, 2): house(2)}))

    first = load(committing_conn, "house", 2)
    second = load(committing_conn, "house", 2)

    assert (first, second) == (2, 0)
    assert count(committing_conn, "votes") == 2


def test_saved_vote_carries_its_category(conn):
    add_members(conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")

    save(conn, vote, positions)

    assert conn.execute("SELECT category FROM votes").fetchone()[0] == "legislation"


def test_category_outside_the_three_is_refused(conn):
    add_members(conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")
    save(conn, vote, positions)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE votes SET category = 'important'")


def test_recategorize_corrects_a_stale_category_and_reports_no_unknowns(committing_conn):
    add_members(committing_conn)
    vote, positions = parse_house(house(), "https://example.test/roll100.xml")
    save(committing_conn, vote, positions)
    committing_conn.execute("UPDATE votes SET category = 'procedural'")

    changed, unknown = recategorize(committing_conn)

    assert (changed, unknown) == (1, [])
    assert committing_conn.execute("SELECT category FROM votes").fetchone()[0] == "legislation"


def test_recategorize_reports_a_question_with_no_rule(committing_conn):
    add_members(committing_conn)
    root = ET.fromstring(HOUSE_XML.replace("On Passage", "On Something New"))
    vote, positions = parse_house(root, "https://example.test/roll100.xml")
    save(committing_conn, vote, positions)

    changed, unknown = recategorize(committing_conn)

    assert changed == 0
    assert [v["question"] for v in unknown] == ["On Something New"]


def test_load_stops_at_the_limit(committing_conn, monkeypatch, no_waiting):
    add_members(committing_conn)
    monkeypatch.setattr("ingest.votes.fetch_xml", fake_fetch({house_url(2, 1): house(1), house_url(2, 2): house(2)}))

    loaded = load(committing_conn, "house", 2, limit=1)

    assert loaded == 1
