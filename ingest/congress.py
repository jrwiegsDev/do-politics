"""Congress's numbering and names.

Which Congress and session a date falls in, what day it is in Washington,
and how Congress writes a bill's name. The web app and the loaders both use
these, so they live here, where the app can import them and nothing here
imports the app.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

FIRST_CONGRESS_YEAR = 1789

# Congress sits in Washington, so its days begin and end on Eastern time.
WASHINGTON = ZoneInfo("America/New_York")


def today_in_washington() -> date:
    """Return today's date in Washington, whatever time zone the server runs in.

    date.today() reads the server's own clock. Render's servers run on UTC,
    which is already tomorrow in Washington from 7 or 8 p.m. Eastern, so the
    district maps, the officials in office and the year a member's pay counts
    toward would all change hours early.
    """
    return datetime.now(WASHINGTON).date()


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


def congresses_since(first: int, day: date) -> list[int]:
    """Return every Congress from first through the one in progress on the given day."""
    return list(range(first, congress_for(day)[0] + 1))


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


# How Congress writes each bill type in front of the number, as in 'H.R. 1'.
BILL_LABELS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}


def bill_label(bill_type: str, bill_number: int) -> str:
    """Return a bill's name as Congress writes it, such as 'H.R. 1' or 'S.J.Res. 12'."""
    return f"{BILL_LABELS[bill_type]} {bill_number}"


def ordinal(number: int) -> str:
    """Return a number as an ordinal, such as '119th', '121st' or '122nd'."""
    suffixes = {1: "st", 2: "nd", 3: "rd"}
    # 11, 12 and 13 take 'th', as in '111th', though they end in 1, 2 and 3.
    if 11 <= number % 100 <= 13:
        suffix = "th"
    else:
        suffix = suffixes.get(number % 10, "th")
    return f"{number}{suffix}"


def bill_name(congress: int, bill_type: str, bill_number: int) -> str:
    """Return a bill's name with its Congress, such as 'S. 5697 (119th Congress)'.

    Bill numbers start over with each Congress, so the Congress is what tells
    H.R. 1 of the 119th from H.R. 1 of the 120th.
    """
    return f"{bill_label(bill_type, bill_number)} ({ordinal(congress)} Congress)"
