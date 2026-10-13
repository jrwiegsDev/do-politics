"""Find a bill and its official summaries."""

import psycopg
from psycopg.rows import dict_row

FIND_BILL_SQL = """
SELECT b.id, b.congress, b.bill_type, b.bill_number, b.title, b.policy_area,
       b.sponsor_bioguide_id, m.name AS sponsor_name,
       b.introduced_on, b.latest_action_on, b.latest_action, b.source_url
FROM bills b
JOIN members m ON m.bioguide_id = b.sponsor_bioguide_id
WHERE b.congress = %(congress)s
  AND b.bill_type = %(bill_type)s
  AND b.bill_number = %(bill_number)s;
"""

FIND_SUMMARIES_SQL = """
SELECT version_code, action_on, action_desc, summary_text
FROM bill_summaries
WHERE bill_id = %(bill_id)s
ORDER BY action_on DESC, version_code DESC;
"""


def find_bill(
    conn: psycopg.Connection, congress: int, bill_type: str, bill_number: int
) -> dict | None:
    """Return a bill with its official summaries, newest stage first.

    Returns None when the bill is not stored. A bill with no official
    summary has an empty list.
    """
    params = {"congress": congress, "bill_type": bill_type, "bill_number": bill_number}
    with conn.cursor(row_factory=dict_row) as cur:
        bill = cur.execute(FIND_BILL_SQL, params).fetchone()
        if bill is None:
            return None

        summaries = cur.execute(FIND_SUMMARIES_SQL, {"bill_id": bill.pop("id")})
        bill["summaries"] = summaries.fetchall()

    return bill
