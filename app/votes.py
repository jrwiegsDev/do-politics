"""Find a member's recent votes."""

import psycopg
from psycopg.rows import dict_row

from ingest.categories import LEGISLATION, NOMINATION, PROCEDURAL

FINAL_CATEGORIES = [LEGISLATION, NOMINATION]
ALL_CATEGORIES = [LEGISLATION, NOMINATION, PROCEDURAL]

FIND_RECENT_VOTES_SQL = """
SELECT v.chamber, v.congress, v.session, v.roll_number, v.voted_at,
       v.question, v.issue, v.title, v.result, v.category, v.source_url,
       p.position
FROM vote_positions p
JOIN votes v ON v.id = p.vote_id
WHERE p.bioguide_id = %(bioguide_id)s
  AND v.category = ANY(%(categories)s)
ORDER BY v.voted_at DESC, v.roll_number DESC
LIMIT %(limit)s;
"""


def find_recent_votes(
    conn: psycopg.Connection,
    bioguide_id: str,
    limit: int = 20,
    categories: list[str] | None = None,
) -> list[dict]:
    """Return a member's most recent votes, newest first, with their position on each.

    Unless other categories are asked for, only final votes on legislation
    and nominations are returned.
    """
    params = {
        "bioguide_id": bioguide_id,
        "categories": categories or FINAL_CATEGORIES,
        "limit": limit,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(FIND_RECENT_VOTES_SQL, params).fetchall()
