"""Find a person who has served in Congress."""

import psycopg
from psycopg.rows import dict_row

FIND_MEMBER_SQL = """
SELECT bioguide_id, name
FROM members
WHERE bioguide_id = %(bioguide_id)s;
"""


def find_member(conn: psycopg.Connection, bioguide_id: str) -> dict | None:
    """Return the member with this Bioguide ID, or None if there is no such person."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(FIND_MEMBER_SQL, {"bioguide_id": bioguide_id}).fetchone()
