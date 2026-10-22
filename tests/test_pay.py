"""Tests for what a member is paid, set beside the days they voted."""

from datetime import date

import pytest

from app.pay import (
    BASE_SALARY,
    paid_through,
    pay_for_session,
    salary,
    session_year,
)

TODAY = date(2026, 10, 21)
REGULAR_START = date(2025, 1, 3)


def test_a_member_with_no_leadership_role_is_paid_the_base():
    assert salary([]) == 174_000 == BASE_SALARY


def test_a_whip_is_paid_the_base():
    assert salary(["Senate Minority Whip"]) == 174_000


@pytest.mark.parametrize(
    ("title", "pay"),
    [
        ("Speaker of the House", 223_500),
        ("President Pro Tempore of the Senate", 193_400),
        ("Senate Majority Leader", 193_400),
        ("Senate Minority Leader", 193_400),
        ("House Majority Leader", 193_400),
        ("House Minority Leader", 193_400),
    ],
)
def test_leaders_are_paid_more(title, pay):
    assert salary([title]) == pay


def test_the_best_paid_role_wins():
    assert salary(["Senate Democratic Conference Chair", "Senate Minority Leader"]) == (
        193_400
    )


@pytest.mark.parametrize(
    ("congress", "session", "year"), [(119, 1, 2025), (119, 2, 2026), (120, 1, 2027)]
)
def test_each_session_sits_in_its_own_year(congress, session, year):
    assert session_year(congress, session) == year


def test_a_whole_year_pays_the_whole_salary():
    assert paid_through(174_000, REGULAR_START, date(2025, 12, 31)) == 174_000


def test_a_term_that_began_years_ago_pays_the_whole_year():
    assert paid_through(174_000, date(2021, 1, 3), date(2025, 12, 31)) == 174_000


def test_part_of_a_year_pays_its_share_by_the_day():
    # January 1 through March 31, 2026: 90 of 365 days.
    assert paid_through(174_000, REGULAR_START, date(2026, 3, 31)) == round(
        174_000 * 90 / 365
    )


def test_a_member_sworn_in_mid_year_is_paid_from_that_day():
    # June 1 through December 31, 2025: 214 of 365 days.
    assert paid_through(174_000, date(2025, 6, 1), date(2025, 12, 31)) == round(
        174_000 * 214 / 365
    )


def test_a_leap_year_divides_by_366_days():
    assert paid_through(174_000, date(2027, 1, 3), date(2028, 12, 31)) == 174_000
    assert paid_through(174_000, date(2027, 1, 3), date(2028, 1, 1)) == round(
        174_000 / 366
    )


def row(session=2, days_voted=90, last_vote_on=date(2026, 3, 31)):
    """Build an attendance row as find_attendance returns one."""
    return {
        "congress": 119,
        "session": session,
        "chamber": "senate",
        "roll_calls": 120,
        "votes_cast": 118,
        "days_voted": days_voted,
        "chamber_days": 95,
        "last_vote_on": last_vote_on,
    }


def test_a_finished_year_counts_in_full():
    pay = pay_for_session(
        row(session=1, days_voted=150, last_vote_on=date(2025, 12, 19)),
        174_000,
        REGULAR_START,
        TODAY,
    )

    assert pay["year"] == 2025
    assert pay["finished"] is True
    assert pay["per_day"] == 1_160  # $174,000 over 150 days


def test_the_current_year_counts_up_to_the_last_vote():
    pay = pay_for_session(row(), 174_000, REGULAR_START, TODAY)

    assert pay["year"] == 2026
    assert pay["finished"] is False
    # Paid for January 1 through March 31, the last vote: 90 of 365 days.
    assert pay["per_day"] == round(round(174_000 * 90 / 365) / 90)


def test_the_attendance_counts_are_kept():
    pay = pay_for_session(row(), 174_000, REGULAR_START, TODAY)

    assert (pay["roll_calls"], pay["votes_cast"], pay["chamber_days"]) == (120, 118, 95)


def test_no_day_voted_gives_no_pay_per_day():
    pay = pay_for_session(row(days_voted=0), 174_000, REGULAR_START, TODAY)

    assert pay["per_day"] is None
