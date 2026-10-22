"""Find a member's recent votes, the topics they cover, and their attendance."""

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
  AND (%(policy_area)s::text IS NULL OR b.policy_area = %(policy_area)s)
ORDER BY v.voted_at DESC, v.roll_number DESC
LIMIT %(limit)s OFFSET %(offset)s;
"""

FIND_POLICY_AREAS_SQL = """
SELECT DISTINCT b.policy_area
FROM vote_positions p
JOIN votes v ON v.id = p.vote_id
JOIN bills b
  ON b.congress = v.congress
 AND b.bill_type = v.bill_type
 AND b.bill_number = v.bill_number
WHERE p.bioguide_id = %(bioguide_id)s
  AND v.category = ANY(%(categories)s)
  AND b.policy_area IS NOT NULL
ORDER BY b.policy_area;
"""

# A vote's day is its date in Washington, where Congress sits. days_voted
# counts the days the member cast at least one vote; chamber_days counts the
# days their chamber held any roll call, and last_vote_on is the latest.
FIND_ATTENDANCE_SQL = """
WITH member_sessions AS (
    SELECT v.congress, v.session, v.chamber,
           count(*) AS roll_calls,
           count(*) FILTER (WHERE p.position <> 'Not Voting') AS votes_cast,
           count(DISTINCT (v.voted_at AT TIME ZONE 'America/New_York')::date)
               FILTER (WHERE p.position <> 'Not Voting') AS days_voted
    FROM vote_positions p
    JOIN votes v ON v.id = p.vote_id
    WHERE p.bioguide_id = %(bioguide_id)s
    GROUP BY v.congress, v.session, v.chamber
),
chamber_sessions AS (
    SELECT congress, session, chamber,
           count(DISTINCT (voted_at AT TIME ZONE 'America/New_York')::date) AS chamber_days,
           max((voted_at AT TIME ZONE 'America/New_York')::date) AS last_vote_on
    FROM votes
    GROUP BY congress, session, chamber
)
SELECT m.congress, m.session, m.chamber, m.roll_calls, m.votes_cast,
       m.days_voted, c.chamber_days, c.last_vote_on
FROM member_sessions m
JOIN chamber_sessions c USING (congress, session, chamber)
ORDER BY m.congress DESC, m.session DESC;
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
    policy_area: str | None = None,
    offset: int = 0,
) -> list[dict]:
    """Return a member's most recent votes, newest first, with their position on each.

    Unless other categories are asked for, only final votes on legislation
    and nominations are returned. Each vote carries the bill it is about,
    when that bill is stored. Naming a policy area keeps only votes on bills
    in that area. The offset skips that many votes, for the page after the first.
    """
    params = {
        "bioguide_id": bioguide_id,
        "categories": categories or FINAL_CATEGORIES,
        "policy_area": policy_area,
        "limit": limit,
        "offset": offset,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        votes = cur.execute(FIND_RECENT_VOTES_SQL, params).fetchall()

    return [nest_bill(vote) for vote in votes]


def find_policy_areas(conn: psycopg.Connection, bioguide_id: str) -> list[str]:
    """Return the policy areas of the bills behind a member's final votes, A to Z."""
    params = {"bioguide_id": bioguide_id, "categories": FINAL_CATEGORIES}
    rows = conn.execute(FIND_POLICY_AREAS_SQL, params).fetchall()
    return [policy_area for (policy_area,) in rows]


def find_attendance(conn: psycopg.Connection, bioguide_id: str) -> list[dict]:
    """Return a member's attendance for each session, newest session first.

    roll_calls is how many roll calls listed the member, and votes_cast is
    how many of those they voted in. Every roll call counts, procedural ones
    and quorum calls included. 'Present' counts as taking part. days_voted
    is how many days they voted on, out of the chamber_days their chamber
    held roll calls, the last of them on last_vote_on.
    """
    params = {"bioguide_id": bioguide_id}
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(FIND_ATTENDANCE_SQL, params).fetchall()
