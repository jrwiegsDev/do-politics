"""Tests for looking up a bill, its official summaries and its generated summary."""

from datetime import date

import pytest
from helpers import add_bill, add_generated, add_members, summary

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
        "generated": None,
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


# Generated summaries. The rule: show the newest approved row, and only beside
# the official summary it was written from.


def generated_text(conn) -> str | None:
    """Return the plain-language text the stored bill would show, if any."""
    generated = stored(conn)["generated"]
    return generated and generated["plain_summary"]


def test_an_approved_summary_comes_with_the_stage_it_was_written_from(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Plain words")

    assert stored(conn)["generated"] == {
        "plain_summary": "Plain words",
        "impact": "How Plain words does you.",
        "source_action_desc": "Introduced in House",
        "source_action_on": date(2025, 1, 15),
    }


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_a_summary_nobody_has_approved_is_not_shown(conn, status):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Plain words", status)

    assert stored(conn)["generated"] is None


def test_an_approved_revision_replaces_the_row_it_revises(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    original = add_generated(conn, "First words")
    add_generated(conn, "Revised words", revised_from=original, minute=1)

    assert generated_text(conn) == "Revised words"


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_a_revision_not_approved_leaves_the_row_it_revises_showing(conn, status):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    original = add_generated(conn, "First words")
    add_generated(conn, "Revised words", status, revised_from=original, minute=1)

    assert generated_text(conn) == "First words"


def test_the_end_of_a_longer_chain_is_shown(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    first = add_generated(conn, "First words")
    second = add_generated(conn, "Second words", revised_from=first, minute=1)
    add_generated(conn, "Third words", revised_from=second, minute=2)

    assert generated_text(conn) == "Third words"


def test_the_newest_approved_summary_wins_across_prompts(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Older prompt", prompt_version="prompt-1")
    add_generated(conn, "Newer prompt", prompt_version="prompt-2", minute=1)

    assert generated_text(conn) == "Newer prompt"


def test_two_written_at_the_same_moment_show_the_later_row(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "Stored first", prompt_version="prompt-1")
    add_generated(conn, "Stored second", prompt_version="prompt-2")

    assert generated_text(conn) == "Stored second"


def test_a_summary_whose_official_summary_is_gone_is_hidden(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED])
    add_generated(conn, "From a stage not stored", version_code="36")

    assert stored(conn)["generated"] is None


def test_a_summary_names_the_stage_it_came_from_not_the_newest(conn):
    add_members(conn)
    add_bill(conn, summaries=[INTRODUCED, LAW])
    add_generated(conn, "Plain words", version_code="00")

    generated = stored(conn)["generated"]

    assert generated["source_action_desc"] == "Introduced in House"
    assert stored(conn)["summaries"][0]["action_desc"] == "Public Law"


def test_only_the_bill_asked_for_brings_its_generated_summary(conn):
    add_members(conn)
    add_bill(conn, 1, summaries=[INTRODUCED])
    add_bill(conn, 2, summaries=[INTRODUCED])
    add_generated(conn, "For bill one", bill_number=1)

    assert stored(conn, 2)["generated"] is None
    assert stored(conn, 1)["generated"]["plain_summary"] == "For bill one"
