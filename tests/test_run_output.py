"""Tests for how the scheduled jobs word what they print at the end of a run.

The rules: a list of details starts with a heading that says how many there
are and what they are; an indented line belongs to the line above it; a
bill is named as the site names it, with its Congress; counts agree with
their nouns.
"""

from contextlib import nullcontext

import pytest

from ingest import bills, photos
from ingest.congress import bill_name
from ingest.logs import counted, print_list


@pytest.mark.parametrize(
    ("count", "singular", "plural", "expected"),
    [
        (1, "bill", None, "1 bill"),
        (563, "bill", None, "563 bills"),
        (0, "bill", None, "0 bills"),
        (1, "summary", "summaries", "1 summary"),
        (2, "summary", "summaries", "2 summaries"),
    ],
)
def test_counted_agrees_with_its_noun(count, singular, plural, expected):
    assert counted(count, singular, plural) == expected


def test_print_list_puts_each_item_under_its_heading(capsys):
    print_list("2 bills skipped:", ["H.R. 1 (119th Congress)", "S. 2 (119th Congress)"])

    assert capsys.readouterr().out == (
        "2 bills skipped:\n  H.R. 1 (119th Congress)\n  S. 2 (119th Congress)\n"
    )


def test_print_list_prints_nothing_when_there_is_nothing(capsys):
    print_list("0 bills skipped:", [])

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("congress", "bill_type", "number", "expected"),
    [
        (119, "s", 5697, "S. 5697 (119th Congress)"),
        (119, "hr", 2785, "H.R. 2785 (119th Congress)"),
        (121, "hjres", 12, "H.J.Res. 12 (121st Congress)"),
    ],
)
def test_bill_name_writes_a_bill_the_way_the_site_does(
    congress, bill_type, number, expected
):
    assert bill_name(congress, bill_type, number) == expected


def run_bills_main(monkeypatch, returned, titles=(0, 0, [])) -> None:
    monkeypatch.setattr("sys.argv", ["ingest.bills"])
    monkeypatch.setattr(bills, "connect", nullcontext)
    monkeypatch.setattr(bills, "load", lambda conn, today, limit: returned)
    monkeypatch.setattr(bills, "catch_up_titles", lambda conn: titles)
    bills.main()


def test_a_bills_run_ends_with_a_heading_over_each_skipped_bill(monkeypatch, capsys):
    # The manual run of 2026-11-20 ended with one bill that had not settled.
    unsettled = [
        "S. 5697 (119th Congress): the bill lists 1 summary, but Congress.gov sent 0"
    ]
    run_bills_main(monkeypatch, (563, [], [], unsettled))

    assert capsys.readouterr().out == (
        "563 bills loaded from congress.gov\n"
        "1 bill skipped until the next run (changed on Congress.gov in the last "
        "24 hours, still being updated there):\n"
        "  S. 5697 (119th Congress): the bill lists 1 summary, but Congress.gov sent 0\n"
    )


def test_a_bills_run_gives_every_kind_of_skip_its_own_heading(monkeypatch, capsys):
    missing = ["H.R. 1 (119th Congress)"]
    unknown = [
        "S. 2 (119th Congress): sponsor N000001",
        "S. 3 (119th Congress): sponsor N000002",
    ]
    run_bills_main(monkeypatch, (1, missing, unknown, []))

    assert capsys.readouterr().out == (
        "1 bill loaded from congress.gov\n"
        "1 bill not found on Congress.gov, skipped:\n"
        "  H.R. 1 (119th Congress)\n"
        "2 bills skipped until the next run (sponsor not in the members table yet):\n"
        "  S. 2 (119th Congress): sponsor N000001\n"
        "  S. 3 (119th Congress): sponsor N000002\n"
    )


def test_a_bills_run_with_nothing_skipped_prints_one_line(monkeypatch, capsys):
    run_bills_main(monkeypatch, (400, [], [], []))

    assert capsys.readouterr().out == "400 bills loaded from congress.gov\n"


def test_a_bills_run_reports_the_titles_catch_up_while_there_is_one(
    monkeypatch, capsys
):
    missed = ["H.R. 7 (119th Congress): TimeoutError"]
    run_bills_main(monkeypatch, (400, [], [], []), titles=(4000, 15921, missed))

    assert capsys.readouterr().out == (
        "400 bills loaded from congress.gov\n"
        "Titles read for 4000 stored bills that had none; 15921 stored bills still to do\n"
        "1 bill whose titles could not be read, tried again next run:\n"
        "  H.R. 7 (119th Congress): TimeoutError\n"
    )


def test_a_bills_run_says_nothing_of_titles_once_caught_up(monkeypatch, capsys):
    run_bills_main(monkeypatch, (400, [], [], []), titles=(0, 0, []))

    assert "Titles" not in capsys.readouterr().out


def test_a_photos_run_lists_the_members_with_no_photo_under_a_heading(
    monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["ingest.photos"])
    monkeypatch.setattr(photos, "connect", nullcontext)
    monkeypatch.setattr(
        photos, "load", lambda conn, limit: (1, 530, ["A000001", "B000002"])
    )

    photos.main()

    assert capsys.readouterr().out == (
        "1 photo new or changed, 530 unchanged\n"
        "2 members with no photo found:\n"
        "  A000001\n"
        "  B000002\n"
    )
