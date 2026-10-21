"""Find a bill, its official summaries, and its approved generated summary."""

from typing import Literal

import psycopg
from psycopg.rows import dict_row

BillType = Literal["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]

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

# The newest approved row is the end of its revision chain: a revision is
# always written after the row it revises, and a draft or rejected revision
# leaves the row before it showing. The inner join hides a generated summary
# whose official summary is gone, since it is only shown beside its source.
GENERATED_SUMMARY_SQL = """
SELECT g.plain_summary, g.impact,
       s.action_desc AS source_action_desc, s.action_on AS source_action_on
FROM generated_summaries g
JOIN bill_summaries s
  ON s.bill_id = g.bill_id AND s.version_code = g.source_version_code
WHERE g.bill_id = %(bill_id)s
  AND g.source = 'crs_summary'
  AND g.status = 'approved'
ORDER BY g.generated_at DESC, g.id DESC
LIMIT 1;
"""


def bill_label(bill_type: str, bill_number: int) -> str:
    """Return a bill's name as Congress writes it, such as 'H.R. 1' or 'S.J.Res. 12'."""
    return f"{BILL_LABELS[bill_type]} {bill_number}"


def find_bill(
    conn: psycopg.Connection, congress: int, bill_type: str, bill_number: int
) -> dict | None:
    """Return a bill with its official summaries, newest stage first.

    Returns None when the bill is not stored. A bill with no official
    summary has an empty list. 'generated' holds the approved plain-language
    summary and the stage it was written from, or None when none is approved.
    """
    params = {"congress": congress, "bill_type": bill_type, "bill_number": bill_number}
    with conn.cursor(row_factory=dict_row) as cur:
        bill = cur.execute(FIND_BILL_SQL, params).fetchone()
        if bill is None:
            return None

        bill_id = bill.pop("id")
        summaries = cur.execute(FIND_SUMMARIES_SQL, {"bill_id": bill_id})
        bill["summaries"] = summaries.fetchall()
        generated = cur.execute(GENERATED_SUMMARY_SQL, {"bill_id": bill_id})
        bill["generated"] = generated.fetchone()

    return bill
