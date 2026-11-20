"""Find a bill, its summaries, its plain-language summary, and its votes, or search for bills."""

from typing import Literal

import psycopg
from psycopg.rows import dict_row

from app.search import TITLE_QUERY

BillType = Literal["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]

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

# An approved row always wins over a draft, and a rejected row is never shown.
# Among approved rows the newest is the end of its revision chain: a revision
# is always written after the row it revises, so a draft or rejected revision
# leaves the approved row before it showing. With nothing approved, the newest
# draft shows, labelled as not yet reviewed. A draft's impact is never read
# out of the database, so "How this does you" cannot reach the page before a
# person approves it. The inner join hides a generated summary whose official
# summary is gone, since it is only shown beside its source.
GENERATED_SUMMARY_SQL = """
SELECT g.status = 'approved' AS reviewed,
       g.plain_summary,
       CASE WHEN g.status = 'approved' THEN g.impact END AS impact,
       s.action_desc AS source_action_desc, s.action_on AS source_action_on
FROM generated_summaries g
JOIN bill_summaries s
  ON s.bill_id = g.bill_id AND s.version_code = g.source_version_code
WHERE g.bill_id = %(bill_id)s
  AND g.source = 'crs_summary'
  AND g.status IN ('approved', 'draft')
ORDER BY g.status = 'approved' DESC, g.generated_at DESC, g.id DESC
LIMIT 1;
"""

# The major steps a bill has taken, oldest first. The Library of Congress
# writes these in plain form ('Introduced in House', 'Passed/agreed to in
# House: ... by voice vote', 'Became Public Law No: 119-21.'), alongside the
# chambers' own fuller records of the same steps, which are left out. It
# sometimes records one step twice under two codes, so steps with the same
# day and words are shown once, where the first of them falls.
MAJOR_ACTIONS_SQL = """
SELECT acted_on, action_text
FROM bill_actions
WHERE bill_id = %(bill_id)s
  AND source = 'Library of Congress'
GROUP BY acted_on, action_text
ORDER BY min(position);
"""

# Every roll call on the bill, newest first, so the top one shows where it
# stands. Votes on amendments are left out: they decide a change someone
# offered, not the bill. 'Aye' and 'No' are how the House records some
# votes, and count the same as 'Yea' and 'Nay'.
BILL_VOTES_SQL = """
SELECT v.chamber, v.roll_number, v.voted_at, v.question, v.result,
       count(*) FILTER (WHERE p.party = 'R' AND p.position IN ('Yea', 'Aye')) AS r_yes,
       count(*) FILTER (WHERE p.party = 'R' AND p.position IN ('Nay', 'No')) AS r_no,
       count(*) FILTER (WHERE p.party = 'D' AND p.position IN ('Yea', 'Aye')) AS d_yes,
       count(*) FILTER (WHERE p.party = 'D' AND p.position IN ('Nay', 'No')) AS d_no,
       count(*) FILTER (WHERE p.party = 'I' AND p.position IN ('Yea', 'Aye')) AS i_yes,
       count(*) FILTER (WHERE p.party = 'I' AND p.position IN ('Nay', 'No')) AS i_no
FROM votes v
JOIN vote_positions p ON p.vote_id = v.id
WHERE v.congress = %(congress)s
  AND v.bill_type = %(bill_type)s
  AND v.bill_number = %(bill_number)s
  AND v.question NOT LIKE 'On Agreeing to the Amendment%%'
GROUP BY v.id
ORDER BY v.voted_at DESC, v.roll_number DESC;
"""

# How one member voted on each roll call on the bill, newest first, with
# amendment votes left out as they are from the party counts.
MEMBER_BILL_VOTES_SQL = """
SELECT v.voted_at, v.question, v.result, p.position
FROM vote_positions p
JOIN votes v ON v.id = p.vote_id
WHERE p.bioguide_id = %(bioguide_id)s
  AND v.congress = %(congress)s
  AND v.bill_type = %(bill_type)s
  AND v.bill_number = %(bill_number)s
  AND v.question NOT LIKE 'On Agreeing to the Amendment%%'
ORDER BY v.voted_at DESC, v.roll_number DESC;
"""


# Bills matching a search, for the Bill Search tile. Laws come first, then
# bills that got a roll call, then the best title matches, so what most
# likely touched someone's life leads. The last keys fix the order, so a
# page and the next never overlap. Whether a bill became law is looked up
# per row through the bill_actions key, which starts with bill_id; the
# bills with a roll call are gathered once, since votes has no index on
# its bill columns.
SEARCH_BILLS_SQL = f"""
WITH voted AS (
    SELECT DISTINCT congress, bill_type, bill_number
    FROM votes
    WHERE bill_type IS NOT NULL
)
SELECT b.congress, b.bill_type, b.bill_number, b.title, b.latest_action_on,
       EXISTS (
           SELECT 1
           FROM bill_actions a
           WHERE a.bill_id = b.id
             AND a.action_type = 'BecameLaw'
       ) AS became_law,
       vb.congress IS NOT NULL AS voted
FROM bills b
LEFT JOIN voted vb
  ON vb.congress = b.congress
 AND vb.bill_type = b.bill_type
 AND vb.bill_number = b.bill_number
WHERE (%(policy_area)s::text IS NULL OR b.policy_area = %(policy_area)s)
  AND (%(bill_type)s::text IS NULL OR b.bill_type = %(bill_type)s)
  AND (%(bill_number)s::int IS NULL OR b.bill_number = %(bill_number)s)
  AND (%(words)s::text IS NULL OR b.title_search @@ {TITLE_QUERY})
ORDER BY became_law DESC,
         voted DESC,
         CASE WHEN %(words)s::text IS NULL THEN 0
              ELSE ts_rank(b.title_search, {TITLE_QUERY})
         END DESC,
         b.latest_action_on DESC NULLS LAST,
         b.congress DESC, b.bill_type, b.bill_number
LIMIT %(limit)s OFFSET %(offset)s;
"""


# The topics (Congress.gov's policy areas) of the stored bills, for Bill
# Search's dropdown.
BILL_TOPICS_SQL = """
SELECT DISTINCT policy_area
FROM bills
WHERE policy_area IS NOT NULL
ORDER BY policy_area;
"""


def find_bill(
    conn: psycopg.Connection, congress: int, bill_type: str, bill_number: int
) -> dict | None:
    """Return a bill with its official summaries, newest stage first.

    Returns None when the bill is not stored. A bill with no official
    summary has an empty list. 'generated' holds the plain-language summary
    to show and the stage it was written from, or None when there is none:
    'reviewed' says whether a person approved it, and 'impact' is None until
    one has.
    'votes' holds each roll call on the bill, newest first, with how many
    members of each party voted yes and no.
    'actions' holds the bill's major steps, oldest first, each with its day
    and words; it is empty until the loader has stored them.
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
        bill["votes"] = cur.execute(BILL_VOTES_SQL, params).fetchall()
        actions = cur.execute(MAJOR_ACTIONS_SQL, {"bill_id": bill_id})
        bill["actions"] = actions.fetchall()

    return bill


def find_member_votes(
    conn: psycopg.Connection,
    congress: int,
    bill_type: str,
    bill_number: int,
    bioguide_id: str,
) -> list[dict]:
    """Return how one member voted on each roll call on a bill, newest first."""
    params = {
        "congress": congress,
        "bill_type": bill_type,
        "bill_number": bill_number,
        "bioguide_id": bioguide_id,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(MEMBER_BILL_VOTES_SQL, params).fetchall()


def search_bills(
    conn: psycopg.Connection,
    limit: int,
    offset: int = 0,
    bill_type: str | None = None,
    bill_number: int | None = None,
    words: str | None = None,
    policy_area: str | None = None,
) -> list[dict]:
    """Return the bills a search asks for, laws first, then bills with a roll call.

    bill_type, bill_number and words come from read_search: a bill as Congress
    writes it, a number alone (any bill type), or words to find in titles,
    each matching title words that start with it. policy_area narrows the
    bills to one topic. Each row says whether the bill became law and
    whether it got a roll call.
    """
    params = {
        "limit": limit,
        "offset": offset,
        "policy_area": policy_area,
        "bill_type": bill_type,
        "bill_number": bill_number,
        "words": words,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(SEARCH_BILLS_SQL, params).fetchall()


def find_bill_topics(conn: psycopg.Connection) -> list[str]:
    """Return the topics of the stored bills, A to Z."""
    return [policy_area for (policy_area,) in conn.execute(BILL_TOPICS_SQL)]
