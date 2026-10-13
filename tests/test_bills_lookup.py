"""Tests for looking up a bill and its official summaries."""

from datetime import date

from helpers import add_bill, add_members, summary

from app.bills import find_bill

INTRODUCED = summary("00", date(2025, 1, 15), "Introduced in House", "As introduced.")
PASSED = summary("36", date(2025, 3, 2), "Passed House", "As passed.")
LAW = summary("49", date(2025, 5, 9), "Public Law", "As enacted.")


def stored(conn, bill_number: int = 9999) -> dict:
    """Look up a made-up House bill that the test has stored."""
    bill = find_bill(conn, 119, "hr", bill_number)
    assert bill is not None
    return bill


def test_bill_comes_back_with_its_sponsors_name(conn):
    add_members(conn)
    add_bill(conn)

    bill = find_bill(conn, 119, "hr", 9999)

    assert bill == {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 9999,
        "title": "Example Act",
        "policy_area": "Energy",
        "sponsor_bioguide_id": "T000001",
        "sponsor_name": "Pat Example",
        "introduced_on": date(2025, 1, 15),
        "latest_action_on": date(2025, 5, 9),
        "latest_action": "Became Public Law.",
        "source_url": "https://example.test/bill/9999",
        "summaries": [],
    }


def test_summaries_come_newest_stage_first(conn):
    add_members(conn)
    add_bill(conn, summaries=[PASSED, LAW, INTRODUCED])

    bill = stored(conn)

    assert [s["action_desc"] for s in bill["summaries"]] == [
        "Public Law",
        "Passed House",
        "Introduced in House",
    ]


def test_two_summaries_on_the_same_day_come_highest_version_first(conn):
    add_members(conn)
    same_day = summary("07", date(2025, 1, 15), "Reported to House", "As reported.")
    add_bill(conn, summaries=[INTRODUCED, same_day])

    bill = stored(conn)

    assert [s["version_code"] for s in bill["summaries"]] == ["07", "00"]


def test_a_summary_carries_its_stage_its_date_and_its_text(conn):
    add_members(conn)
    add_bill(conn, summaries=[LAW])

    bill = stored(conn)

    assert bill["summaries"] == [
        {
            "version_code": "49",
            "action_on": date(2025, 5, 9),
            "action_desc": "Public Law",
            "summary_text": "As enacted.",
        }
    ]


def test_the_original_html_is_not_returned(conn):
    add_members(conn)
    add_bill(conn, summaries=[LAW])

    bill = stored(conn)

    assert "summary_html_source" not in bill["summaries"][0]


def test_only_the_bill_asked_for_is_returned(conn):
    add_members(conn)
    add_bill(conn, 1, summaries=[INTRODUCED])
    add_bill(conn, 2, summaries=[LAW])

    bill = stored(conn, 2)

    assert bill["bill_number"] == 2
    assert [s["action_desc"] for s in bill["summaries"]] == ["Public Law"]


def test_a_bill_that_is_not_stored_is_none(conn):
    add_members(conn)
    add_bill(conn)

    assert find_bill(conn, 119, "hr", 1) is None
    assert find_bill(conn, 119, "s", 9999) is None
    assert find_bill(conn, 118, "hr", 9999) is None
