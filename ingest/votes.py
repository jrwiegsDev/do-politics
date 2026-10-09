"""Load roll call votes from the House Clerk and the Senate into votes and vote_positions.

Run ingest.members first: every position must belong to a known member.

Usage:
    uv run --env-file .env python -m ingest.votes --chamber house --session 2
    uv run --env-file .env python -m ingest.votes --chamber senate --session 2
    uv run --env-file .env python -m ingest.votes --chamber house --session 2 --limit 3
    uv run --env-file .env python -m ingest.votes --recategorize
"""

import argparse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, time
from itertools import islice
from time import sleep
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from ingest.categories import categorize, is_known
from ingest.db import connect
from ingest.runs import record_run

CONGRESS = 119

# A session is filed under the year it starts in. A vote cast in the first
# days of the following January, before the next Congress convenes, still
# belongs to it and is numbered and filed with the rest of that session.
SESSION_YEARS = {1: 2025, 2: 2026}

# Both chambers publish vote times as Eastern time.
EASTERN = ZoneInfo("America/New_York")

SOURCES = {"house": "house-clerk", "senate": "senate-lis"}

# Sent with every request, so the chambers' webmasters can see who is asking.
USER_AGENT = "DoPolitics (https://github.com/jrwiegsDev/do-politics)"
PAUSE_SECONDS = 0.5

LAST_LOADED_SQL = """
SELECT coalesce(max(roll_number), 0)
FROM votes
WHERE chamber = %(chamber)s
  AND congress = %(congress)s
  AND session = %(session)s;
"""

BIOGUIDE_BY_LIS_SQL = """
SELECT lis_id, bioguide_id
FROM members
WHERE lis_id IS NOT NULL;
"""

UPSERT_VOTE_SQL = """
INSERT INTO votes (
    chamber, congress, session, roll_number, voted_at,
    question, vote_type, issue, title, result, source_url, category
)
VALUES (
    %(chamber)s, %(congress)s, %(session)s, %(roll_number)s, %(voted_at)s,
    %(question)s, %(vote_type)s, %(issue)s, %(title)s, %(result)s, %(source_url)s, %(category)s
)
ON CONFLICT (chamber, congress, session, roll_number)
DO UPDATE SET
    voted_at = EXCLUDED.voted_at,
    question = EXCLUDED.question,
    vote_type = EXCLUDED.vote_type,
    issue = EXCLUDED.issue,
    title = EXCLUDED.title,
    result = EXCLUDED.result,
    source_url = EXCLUDED.source_url,
    category = EXCLUDED.category,
    updated_at = now()
RETURNING id;
"""

UPSERT_POSITION_SQL = """
INSERT INTO vote_positions (vote_id, bioguide_id, position, party, state)
VALUES (%(vote_id)s, %(bioguide_id)s, %(position)s, %(party)s, %(state)s)
ON CONFLICT (vote_id, bioguide_id)
DO UPDATE SET
    position = EXCLUDED.position,
    party = EXCLUDED.party,
    state = EXCLUDED.state;
"""

STORED_VOTES_SQL = """
SELECT id, chamber, question, title, category, source_url
FROM votes
ORDER BY id;
"""

SET_CATEGORY_SQL = """
UPDATE votes
SET category = %(category)s,
    updated_at = now()
WHERE id = %(id)s;
"""


def house_url(session: int, number: int) -> str:
    return f"https://clerk.house.gov/evs/{SESSION_YEARS[session]}/roll{number:03d}.xml"


def senate_url(session: int, number: int) -> str:
    folder = f"vote{CONGRESS}{session}"
    return f"https://www.senate.gov/legislative/LIS/roll_call_votes/{folder}/vote_{CONGRESS}_{session}_{number:05d}.xml"


def senate_menu_url(session: int) -> str:
    return f"https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{CONGRESS}_{session}.xml"


def fetch_xml(url: str) -> ET.Element:
    """Download one XML document and return its root element."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return ET.fromstring(response.read())


def text_or_none(value: str | None) -> str | None:
    """Return text with outer whitespace removed, or None when nothing is left."""
    value = (value or "").strip()
    return value or None


def parse_house(root: ET.Element, source_url: str) -> tuple[dict, list[dict]]:
    """Turn one House Clerk roll call into a votes row and its position rows."""
    meta = root.find("vote-metadata")
    day = datetime.strptime(meta.findtext("action-date"), "%d-%b-%Y")
    clock = time.fromisoformat(meta.find("action-time").get("time-etz"))

    vote = {
        "chamber": "house",
        "congress": int(meta.findtext("congress")),
        "session": int(meta.findtext("session")[0]),
        "roll_number": int(meta.findtext("rollcall-num")),
        "voted_at": datetime.combine(day, clock, tzinfo=EASTERN),
        "question": text_or_none(meta.findtext("vote-question")),
        "vote_type": text_or_none(meta.findtext("vote-type")),
        "issue": text_or_none(meta.findtext("legis-num")),
        "title": text_or_none(meta.findtext("vote-desc")),
        "result": meta.findtext("vote-result"),
        "source_url": source_url,
    }

    positions = []
    for recorded in root.iter("recorded-vote"):
        legislator = recorded.find("legislator")
        positions.append(
            {
                "bioguide_id": legislator.get("name-id"),
                "position": recorded.findtext("vote"),
                "party": legislator.get("party"),
                "state": legislator.get("state"),
            }
        )

    return vote, positions


def parse_senate(root: ET.Element, source_url: str, bioguide_by_lis: dict[str, str]) -> tuple[dict, list[dict]]:
    """Turn one Senate roll call into a votes row and its position rows."""
    voted = " ".join(root.findtext("vote_date").split())

    vote = {
        "chamber": "senate",
        "congress": int(root.findtext("congress")),
        "session": int(root.findtext("session")),
        "roll_number": int(root.findtext("vote_number")),
        "voted_at": datetime.strptime(voted, "%B %d, %Y, %I:%M %p").replace(tzinfo=EASTERN),
        "question": text_or_none(root.findtext("question")),
        "vote_type": None,
        "issue": text_or_none(root.findtext("document/document_name")),
        "title": text_or_none(root.findtext("vote_title")),
        "result": root.findtext("vote_result"),
        "source_url": source_url,
    }

    positions = []
    for member in root.iter("member"):
        positions.append(
            {
                "bioguide_id": bioguide_by_lis[member.findtext("lis_member_id")],
                "position": member.findtext("vote_cast"),
                "party": member.findtext("party"),
                "state": member.findtext("state"),
            }
        )

    return vote, positions


def house_votes(session: int, after: int) -> Iterator[tuple[dict, list[dict]]]:
    """Yield each House roll call after the given number, stopping at the first one missing.

    The Clerk answers HTTP 200 with a small error document for a roll call
    that does not exist, so the root element is what tells a vote from a miss.
    A miss only ends this run: the next run asks for the same number again.
    """
    number = after + 1
    while True:
        url = house_url(session, number)
        root = fetch_xml(url)
        if root.tag != "rollcall-vote":
            return

        yield parse_house(root, url)
        number += 1
        sleep(PAUSE_SECONDS)


def senate_votes(session: int, after: int, bioguide_by_lis: dict[str, str]) -> Iterator[tuple[dict, list[dict]]]:
    """Yield each Senate vote after the given number, oldest first, from the session's list."""
    menu = fetch_xml(senate_menu_url(session))
    numbers = sorted(int(vote.findtext("vote_number")) for vote in menu.iter("vote"))

    for number in numbers:
        if number > after:
            url = senate_url(session, number)
            yield parse_senate(fetch_xml(url), url, bioguide_by_lis)
            sleep(PAUSE_SECONDS)


def check_belongs(vote: dict, chamber: str, session: int) -> None:
    """Refuse a vote whose own labels disagree with the session that was asked for."""
    asked = (chamber, CONGRESS, session)
    found = (vote["chamber"], vote["congress"], vote["session"])
    if found != asked:
        raise ValueError(f"{vote['source_url']} says it is {found}; expected {asked}")


def save(conn, vote: dict, positions: list[dict]) -> None:
    """Upsert one vote with its category, then its positions under the id the database gave it."""
    vote_id = conn.execute(UPSERT_VOTE_SQL, vote | {"category": categorize(vote)}).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_POSITION_SQL, [position | {"vote_id": vote_id} for position in positions])


def load(conn, chamber: str, session: int, limit: int | None = None) -> int:
    """Load every vote newer than the last one stored, and return how many were loaded.

    Each vote is committed with its positions as soon as it is saved. A run
    that fails partway is recorded as failed and keeps the votes already
    saved, so the next run carries on from where this one stopped.
    """
    with record_run(conn, f"votes-{chamber}", SOURCES[chamber]) as run:
        identity = {"chamber": chamber, "congress": CONGRESS, "session": session}
        after = conn.execute(LAST_LOADED_SQL, identity).fetchone()[0]

        if chamber == "house":
            found = house_votes(session, after)
        else:
            found = senate_votes(session, after, dict(conn.execute(BIOGUIDE_BY_LIS_SQL).fetchall()))

        for vote, positions in islice(found, limit):
            check_belongs(vote, chamber, session)
            save(conn, vote, positions)
            conn.commit()
            run.rows_loaded += 1
            print(f"{chamber} {vote['roll_number']}: {len(positions)} positions")
            if not is_known(vote):
                print(f"  new question wording, counted as procedural: {vote['question']!r}")

    return run.rows_loaded


def recategorize(conn) -> tuple[int, list[dict]]:
    """Apply the current category rules to every stored vote, as one recorded run.

    Nothing is downloaded: the question and title are already stored. Returns
    how many votes changed category, and the votes whose question has no rule.
    """
    with record_run(conn, "votes-categories", "ingest.categories") as run:
        with conn.cursor(row_factory=dict_row) as cur:
            votes = cur.execute(STORED_VOTES_SQL).fetchall()

        changed = [vote for vote in votes if categorize(vote) != vote["category"]]
        with conn.cursor() as cur:
            cur.executemany(SET_CATEGORY_SQL, [{"id": vote["id"], "category": categorize(vote)} for vote in changed])
        run.rows_loaded = len(changed)

    return len(changed), [vote for vote in votes if not is_known(vote)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chamber", choices=sorted(SOURCES))
    parser.add_argument("--session", type=int, choices=sorted(SESSION_YEARS))
    parser.add_argument("--limit", type=int, help="stop after this many votes")
    parser.add_argument("--recategorize", action="store_true", help="re-apply the category rules to stored votes; downloads nothing")
    args = parser.parse_args()

    if args.recategorize:
        with connect() as conn:
            changed, unknown = recategorize(conn)
        print(f"{changed} votes changed category")
        for vote in unknown:
            print(f"  no rule for {vote['chamber']} question {vote['question']!r}: {vote['source_url']}")
        return

    if args.chamber is None or args.session is None:
        parser.error("--chamber and --session are required unless --recategorize is given")

    with connect() as conn:
        loaded = load(conn, args.chamber, args.session, args.limit)
    print(f"{loaded} {args.chamber} votes loaded from {SOURCES[args.chamber]}")


if __name__ == "__main__":
    main()
