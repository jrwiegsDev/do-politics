"""Tests for working out the Congress and session from a date, and today in Washington."""

from datetime import UTC, date, datetime

import pytest

from ingest.congress import (
    congress_for,
    congresses_since,
    previous_session,
    session_year,
    sessions_to_check,
    today_in_washington,
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
    ("day", "expected"),
    [
        (date(2026, 11, 17), [119]),
        # January 2 still belongs to the 119th; the 120th is seated on the 3rd.
        (date(2027, 1, 2), [119]),
        (date(2027, 1, 3), [119, 120]),
    ],
)
def test_congresses_since_runs_through_the_one_in_progress(day, expected):
    assert congresses_since(119, day) == expected


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


class FrozenClock(datetime):
    """A datetime whose now() always gives the same instant, converted to the zone asked for."""

    instant = datetime(2027, 1, 3, 0, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        # 7 p.m. Eastern on January 2 is already January 3 in UTC, the day
        # the 120th Congress is seated. Washington is still on January 2.
        (datetime(2027, 1, 3, 0, 0, tzinfo=UTC), date(2027, 1, 2)),
        # New Year's Eve, 8 p.m. Eastern: already the new year in UTC.
        (datetime(2027, 1, 1, 1, 0, tzinfo=UTC), date(2026, 12, 31)),
        # Midnight in Washington: the new day has begun there too.
        (datetime(2027, 1, 3, 5, 0, tzinfo=UTC), date(2027, 1, 3)),
        # Daylight saving time: 8 p.m. Eastern in July is midnight UTC.
        (datetime(2026, 7, 5, 0, 0, tzinfo=UTC), date(2026, 7, 4)),
    ],
)
def test_today_is_washington_s_date_not_the_server_s(monkeypatch, instant, expected):
    monkeypatch.setattr(FrozenClock, "instant", instant)
    monkeypatch.setattr("ingest.congress.datetime", FrozenClock)

    assert today_in_washington() == expected
