"""Find a member's recent votes."""

import psycopg
from psycopg.rows import dict_row

from ingest.categories import LEGISLATION, NOMINATION, PROCEDURAL

FINAL_CATEGORIES = [LEGISLATION, NOMINATION]
ALL_CATEGORIES = [LEGISLATION, NOMINATION, PROCEDURAL]

FIND_RECENT_VOTES_SQL = """
SELECT v.chamber, v.congress, v.session, v.roll_number, v.voted_at,
       v.question, v.issue, v.title, v.result, v.category, v.source_url,
       p.position,
       b.bill_type, b.bill_number, b.title AS bill_title,
       b.policy_area AS bill_policy_area, b.source_url AS bill_source_url
FROM vote_positions p
JOIN votes v ON v.id = p.vote_id
LEFT JOIN bills b
  ON b.congress = v.congress
 AND b.bill_type = v.bill_type
 AND b.bill_number = v.bill_number
WHERE p.bioguide_id = %(bioguide_id)s
  AND v.category = ANY(%(categories)s)
ORDER BY v.voted_at DESC, v.roll_number DESC
LIMIT %(limit)s;
"""


def nest_bill(vote: dict) -> dict:
    """Gather a vote's bill columns into one 'bill' entry, or None with no stored bill."""
    bill = {
        "congress": vote["congress"],
        "bill_type": vote.pop("bill_type"),
        "bill_number": vote.pop("bill_number"),
        "title": vote.pop("bill_title"),
        "policy_area": vote.pop("bill_policy_area"),
        "source_url": vote.pop("bill_source_url"),
    }
    vote["bill"] = bill if bill["bill_type"] else None
    return vote


def find_recent_votes(
    conn: psycopg.Connection,
    bioguide_id: str,
    limit: int = 20,
    categories: list[str] | None = None,
) -> list[dict]:
    """Return a member's most recent votes, newest first, with their position on each.

    Unless other categories are asked for, only final votes on legislation
    and nominations are returned. Each vote carries the bill it is about,
    when that bill is stored.
    """
    params = {
        "bioguide_id": bioguide_id,
        "categories": categories or FINAL_CATEGORIES,
        "limit": limit,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        votes = cur.execute(FIND_RECENT_VOTES_SQL, params).fetchall()

    return [nest_bill(vote) for vote in votes]
