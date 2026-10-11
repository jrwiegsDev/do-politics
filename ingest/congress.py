"""Work out which Congress and session a date falls in."""

from datetime import date

FIRST_CONGRESS_YEAR = 1789


def congress_for(day: date) -> tuple[int, int]:
    """Return the (congress, session) in progress on the given day.

    A Congress is seated on January 3, so January 1 and 2 still belong to
    the session of the year before. That date holds from 1935 on; earlier
    Congresses began on March 4, which this does not account for.
    """
    year = day.year - 1 if (day.month, day.day) < (1, 3) else day.year
    congress = (year - FIRST_CONGRESS_YEAR) // 2 + 1
    session = 1 if year % 2 == 1 else 2
    return congress, session


def session_year(congress: int, session: int) -> int:
    """Return the year a session is filed under, such as 2026 for the 119th's second."""
    return FIRST_CONGRESS_YEAR + (congress - 1) * 2 + (session - 1)


def previous_session(congress: int, session: int) -> tuple[int, int]:
    """Return the (congress, session) that came just before the given one."""
    if session == 2:
        return congress, 1
    return congress - 1, 2


def sessions_to_check(day: date) -> list[tuple[int, int]]:
    """Return the session before the current one, then the current one.

    A vote cast in a session's last days can be published after the next
    session has begun, so a scheduled run looks at both.
    """
    current = congress_for(day)
    return [previous_session(*current), current]
