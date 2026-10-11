"""Tests for working out the Congress and session from a date."""

from datetime import date

import pytest

from ingest.congress import (
    congress_for,
    previous_session,
    session_year,
    sessions_to_check,
)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2025, 1, 3), (119, 1)),
        (date(2025, 12, 31), (119, 1)),
        (date(2026, 1, 2), (119, 1)),
        (date(2026, 1, 3), (119, 2)),
        (date(2026, 10, 10), (119, 2)),
        (date(2027, 1, 2), (119, 2)),
        (date(2027, 1, 3), (120, 1)),
        (date(1789, 3, 4), (1, 1)),
    ],
)
def test_a_date_gives_its_congress_and_session(day, expected):
    assert congress_for(day) == expected


@pytest.mark.parametrize(
    ("congress", "session", "year"),
    [(119, 1, 2025), (119, 2, 2026), (120, 1, 2027), (1, 1, 1789)],
)
def test_a_session_is_filed_under_the_year_it_starts_in(congress, session, year):
    assert session_year(congress, session) == year


def test_session_year_undoes_congress_for():
    for year in range(1935, 2040):
        congress, session = congress_for(date(year, 6, 1))

        assert session_year(congress, session) == year


def test_the_session_before_a_second_session_is_the_first():
    assert previous_session(119, 2) == (119, 1)


def test_the_session_before_a_first_session_is_in_the_congress_before():
    assert previous_session(120, 1) == (119, 2)


def test_a_run_checks_the_earlier_session_and_then_the_current_one():
    assert sessions_to_check(date(2027, 1, 5)) == [(119, 2), (120, 1)]
