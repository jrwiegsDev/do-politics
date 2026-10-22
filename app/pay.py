"""What a member of Congress is paid, set beside the days they voted."""

from datetime import date

# Congressional Research Service, "Congressional Salaries and Allowances: In
# Brief" (RL30064), updated September 10, 2026. Pay has stayed at these
# amounts since 2009. Party whips and other leadership roles are paid the
# base salary.
BASE_SALARY = 174_000
LEADERSHIP_SALARIES = {
    "Speaker of the House": 223_500,
    "President Pro Tempore of the Senate": 193_400,
    "Senate Majority Leader": 193_400,
    "Senate Minority Leader": 193_400,
    "House Majority Leader": 193_400,
    "House Minority Leader": 193_400,
}
PAY_SOURCE_URL = "https://www.congress.gov/crs-product/RL30064"

# The federal minimum wage, $7.25 an hour, in a full-time job: 8 hours a
# day and 2,080 hours a year.
MINIMUM_WAGE_DAY = 58
MINIMUM_WAGE_YEAR = 15_080


def salary(leadership_titles: list[str]) -> int:
    """Return a member's yearly salary: the most any role they hold pays, or the base."""
    return max(
        (LEADERSHIP_SALARIES.get(title, BASE_SALARY) for title in leadership_titles),
        default=BASE_SALARY,
    )


def session_year(congress: int, session: int) -> int:
    """Return the calendar year a session of Congress sits in, such as 2025 for 119-1."""
    return 1787 + 2 * congress + session - 1


def paid_through(yearly: int, term_start: date, through: date) -> int:
    """Return what a member was paid in a year, from its start up to and including through.

    Pay builds up day by day. A term that begins on January 3, as regular
    terms do, counts from January 1; a member sworn in later in the year,
    after a special election, is paid from that day.
    """
    year_start = date(through.year, 1, 1)
    start = term_start if term_start > date(through.year, 1, 3) else year_start
    days_in_year = (date(through.year + 1, 1, 1) - year_start).days
    days_paid = max((through - start).days + 1, 0)
    return round(yearly * days_paid / days_in_year)


def pay_for_session(row: dict, yearly: int, term_start: date, today: date) -> dict:
    """Return an attendance row with the year and the pay for each day voted added.

    A year that is over counts in full. The current year counts up to the
    last day the chamber held a vote, so the pay and the days voted cover
    the same stretch. per_day is None when the member voted on no day.
    """
    year = session_year(row["congress"], row["session"])
    finished = year < today.year
    through = date(year, 12, 31) if finished else row["last_vote_on"]
    paid = paid_through(yearly, term_start, through)
    per_day = round(paid / row["days_voted"]) if row["days_voted"] else None
    return row | {"year": year, "finished": finished, "per_day": per_day}
