"""Daily usage totals: how many lookups, bill views and searches, never who.

Each function adds 1 to today's row, by Central time. The tables keep
nothing else: no address, IP address, search words or time of day. A count
that can't be saved is logged and dropped, so counting never changes what a
visitor gets back.
"""

import logging
from datetime import UTC, date, datetime
from typing import Literal, LiteralString
from zoneinfo import ZoneInfo

import psycopg

logger = logging.getLogger(__name__)

# A day of counts ends at midnight in Illinois, not at midnight UTC.
CENTRAL = ZoneInfo("America/Chicago")

Outcome = Literal[
    "found", "not_found", "no_district", "unavailable", "rate_limited", "invalid"
]
OpenedFrom = Literal["search", "votes"]

COUNT_LOOKUP_SQL = """
INSERT INTO lookup_counts (day, outcome, state_fips, lookups)
VALUES (%(day)s, %(outcome)s, %(state_fips)s, 1)
ON CONFLICT (day, outcome, state_fips)
DO UPDATE SET lookups = lookup_counts.lookups + 1;
"""

COUNT_BILL_VIEW_SQL = """
INSERT INTO bill_view_counts (day, bill_id, opened_from, views)
SELECT %(day)s, id, %(opened_from)s, 1
FROM bills
WHERE congress = %(congress)s
  AND bill_type = %(bill_type)s
  AND bill_number = %(bill_number)s
ON CONFLICT (day, bill_id, opened_from)
DO UPDATE SET views = bill_view_counts.views + 1;
"""

COUNT_SEARCH_SQL = """
INSERT INTO search_counts (day, requests, empty_requests)
VALUES (%(day)s, 1, %(empty)s)
ON CONFLICT (day)
DO UPDATE SET requests = search_counts.requests + 1,
              empty_requests = search_counts.empty_requests + EXCLUDED.empty_requests;
"""


def today(now: datetime | None = None) -> date:
    """Return the date in Central time, now or at the moment given."""
    return (now or datetime.now(UTC)).astimezone(CENTRAL).date()


def add_one(conn: psycopg.Connection, sql: LiteralString, params: dict) -> None:
    """Run one count, logging instead of failing when it can't be saved.

    The count runs in a savepoint inside the request's transaction, so a
    failed count undoes only itself and the request carries on.
    """
    try:
        with conn.transaction():
            conn.execute(sql, params)
    except psycopg.Error:
        logger.exception("A usage count could not be saved")


def count_lookup(
    conn: psycopg.Connection,
    outcome: Outcome,
    state_fips: str | None = None,
    now: datetime | None = None,
) -> None:
    """Add 1 to today's lookups for this outcome, and its state when found."""
    params = {"day": today(now), "outcome": outcome, "state_fips": state_fips}
    add_one(conn, COUNT_LOOKUP_SQL, params)


def count_bill_view(
    conn: psycopg.Connection,
    congress: int,
    bill_type: str,
    bill_number: int,
    opened_from: OpenedFrom,
    now: datetime | None = None,
) -> None:
    """Add 1 to today's views of a bill, by where it was opened from."""
    params = {
        "day": today(now),
        "congress": congress,
        "bill_type": bill_type,
        "bill_number": bill_number,
        "opened_from": opened_from,
    }
    add_one(conn, COUNT_BILL_VIEW_SQL, params)


def count_search(
    conn: psycopg.Connection, found_nothing: bool, now: datetime | None = None
) -> None:
    """Add 1 to today's search requests, and to the empty ones if it found nothing."""
    params = {"day": today(now), "empty": int(found_nothing)}
    add_one(conn, COUNT_SEARCH_SQL, params)
