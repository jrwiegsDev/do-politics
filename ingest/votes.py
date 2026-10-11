"""Load roll call votes from the House Clerk and the Senate into votes and vote_positions.

Run ingest.members first: every position must belong to a known member.

Usage:
    uv run --env-file .env python -m ingest.votes --chamber house
    uv run --env-file .env python -m ingest.votes --chamber senate --congress 119 --session 1
    uv run --env-file .env python -m ingest.votes --chamber house --limit 3

With no --congress and --session, the current session is worked out from
today's date, and the session before it is checked as well.
    uv run --env-file .env python -m ingest.votes --recategorize
"""

import argparse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date, datetime, time
from itertools import islice
from time import sleep
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from ingest.categories import categorize, is_known
from ingest.congress import session_year, sessions_to_check
from ingest.db import connect
from ingest.runs import record_run

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


class NotPublished(Exception):
    """The address asked for does not exist on the chamber's site."""


def house_url(congress: int, session: int, number: int) -> str:
    # A session is filed under the year it starts in. A vote cast in the first
    # days of the following January, before the next Congress convenes, still
    # belongs to it and is numbered and filed with the rest of that session.
    year = session_year(congress, session)
    return f"https://clerk.house.gov/evs/{year}/roll{number:03d}.xml"


def senate_url(congress: int, session: int, number: int) -> str:
    folder = f"vote{congress}{session}"
    return f"https://www.senate.gov/legislative/LIS/roll_call_votes/{folder}/vote_{congress}_{session}_{number:05d}.xml"


def senate_menu_url(congress: int, session: int) -> str:
    return f"https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"


def fetch_xml(url: str) -> ET.Element:
    """Download one XML document and return its root element.

    The Senate answers an address that does not exist by redirecting to a
    "file not found" web page with a normal success status, so ending up
    anywhere but the address asked for is what marks a miss.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.url != url:
            raise NotPublished(url)
        return ET.fromstring(response.read())


def text_or_none(value: str | None) -> str | None:
    """Return text with outer whitespace removed, or None when nothing is left."""
    value = (value or "").strip()
    return value or None


def required_child(parent: ET.Element, path: str) -> ET.Element:
    """Return the element at path, or raise an error naming what is missing."""
    found = parent.find(path)
    if found is None:
        raise ValueError(f"<{parent.tag}> has no <{path}> element")
    return found


def required_text(parent: ET.Element, path: str) -> str:
    """Return the text of the element at path, which must exist and hold text."""
    value = required_child(parent, path).text
    if value is None:
        raise ValueError(f"<{path}> in <{parent.tag}> is empty")
    return value


def required_attr(element: ET.Element, name: str) -> str:
    """Return the named attribute, or raise an error naming what is missing."""
    value = element.get(name)
    if value is None:
        raise ValueError(f"<{element.tag}> has no {name} attribute")
    return value


def parse_house(root: ET.Element, source_url: str) -> tuple[dict, list[dict]]:
    """Turn one House Clerk roll call into a votes row and its position rows."""
    meta = required_child(root, "vote-metadata")
    day = datetime.strptime(required_text(meta, "action-date"), "%d-%b-%Y")
    clock = time.fromisoformat(
        required_attr(required_child(meta, "action-time"), "time-etz")
    )

    vote = {
        "chamber": "house",
        "congress": int(required_text(meta, "congress")),
        "session": int(required_text(meta, "session")[0]),
        "roll_number": int(required_text(meta, "rollcall-num")),
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
        legislator = required_child(recorded, "legislator")
        bioguide_id = required_attr(legislator, "name-id")
        positions.append(
            {
                "bioguide_id": bioguide_id,
                "position": recorded.findtext("vote"),
                "party": legislator.get("party"),
                "state": legislator.get("state"),
            }
        )

    return vote, positions


def parse_senate(
    root: ET.Element, source_url: str, bioguide_by_lis: dict[str, str]
) -> tuple[dict, list[dict]]:
    """Turn one Senate roll call into a votes row and its position rows."""
    voted = " ".join(required_text(root, "vote_date").split())

    vote = {
        "chamber": "senate",
        "congress": int(required_text(root, "congress")),
        "session": int(required_text(root, "session")),
        "roll_number": int(required_text(root, "vote_number")),
        "voted_at": datetime.strptime(voted, "%B %d, %Y, %I:%M %p").replace(
            tzinfo=EASTERN
        ),
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
                "bioguide_id": bioguide_by_lis[required_text(member, "lis_member_id")],
                "position": member.findtext("vote_cast"),
                "party": member.findtext("party"),
                "state": member.findtext("state"),
            }
        )

    return vote, positions


def house_votes(
    congress: int, session: int, after: int
) -> Iterator[tuple[dict, list[dict]]]:
    """Yield each House roll call after the given number, stopping at the first one missing.

    The Clerk answers HTTP 200 with a small error document for a roll call
    that does not exist, so the root element is what tells a vote from a miss.
    A miss only ends this run: the next run asks for the same number again.
    """
    number = after + 1
    while True:
        url = house_url(congress, session, number)
        root = fetch_xml(url)
        if root.tag != "rollcall-vote":
            return

        yield parse_house(root, url)
        number += 1
        sleep(PAUSE_SECONDS)


def senate_votes(
    congress: int, session: int, after: int, bioguide_by_lis: dict[str, str]
) -> Iterator[tuple[dict, list[dict]]]:
    """Yield each Senate vote after the given number, oldest first, from the session's list.

    A session that has not held a vote yet has no list, which is not an error:
    there is simply nothing to load. A vote that is on the list but cannot be
    fetched is an error, and fails the run.
    """
    try:
        menu = fetch_xml(senate_menu_url(congress, session))
    except NotPublished:
        return

    numbers = sorted(
        int(required_text(vote, "vote_number")) for vote in menu.iter("vote")
    )

    for number in numbers:
        if number > after:
            url = senate_url(congress, session, number)
            yield parse_senate(fetch_xml(url), url, bioguide_by_lis)
            sleep(PAUSE_SECONDS)


def check_belongs(vote: dict, chamber: str, congress: int, session: int) -> None:
    """Refuse a vote whose own labels disagree with the session that was asked for."""
    asked = (chamber, congress, session)
    found = (vote["chamber"], vote["congress"], vote["session"])
    if found != asked:
        raise ValueError(f"{vote['source_url']} says it is {found}; expected {asked}")


def save(conn, vote: dict, positions: list[dict]) -> None:
    """Upsert one vote with its category, then its positions under the id the database gave it."""
    vote_id = conn.execute(
        UPSERT_VOTE_SQL, vote | {"category": categorize(vote)}
    ).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            UPSERT_POSITION_SQL,
            [position | {"vote_id": vote_id} for position in positions],
        )


def load(
    conn, chamber: str, congress: int, session: int, limit: int | None = None
) -> int:
    """Load one session's votes newer than the last one stored, and return how many.

    Each vote is committed with its positions as soon as it is saved. A run
    that fails partway is recorded as failed and keeps the votes already
    saved, so the next run carries on from where this one stopped.
    """
    with record_run(conn, f"votes-{chamber}", SOURCES[chamber]) as run:
        identity = {"chamber": chamber, "congress": congress, "session": session}
        after = conn.execute(LAST_LOADED_SQL, identity).fetchone()[0]

        if chamber == "house":
            found = house_votes(congress, session, after)
        else:
            bioguide_by_lis = dict(conn.execute(BIOGUIDE_BY_LIS_SQL).fetchall())
            found = senate_votes(congress, session, after, bioguide_by_lis)

        for vote, positions in islice(found, limit):
            check_belongs(vote, chamber, congress, session)
            save(conn, vote, positions)
            conn.commit()
            run.rows_loaded += 1
            print(f"{chamber} {vote['roll_number']}: {len(positions)} positions")
            if not is_known(vote):
                print(
                    f"  new question wording, counted as procedural: {vote['question']!r}"
                )

    return run.rows_loaded


def load_current(conn, chamber: str, today: date, limit: int | None = None) -> int:
    """Load new votes from the session in progress and the one before it.

    The earlier session is checked because a vote cast in its last days can
    be published after the next session has begun.
    """
    return sum(
        load(conn, chamber, congress, session, limit)
        for congress, session in sessions_to_check(today)
    )


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
            cur.executemany(
                SET_CATEGORY_SQL,
                [{"id": vote["id"], "category": categorize(vote)} for vote in changed],
            )
        run.rows_loaded = len(changed)

    return len(changed), [vote for vote in votes if not is_known(vote)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chamber", choices=sorted(SOURCES))
    parser.add_argument("--congress", type=int, help="such as 119; needs --session")
    parser.add_argument("--session", type=int, choices=[1, 2], help="needs --congress")
    parser.add_argument("--limit", type=int, help="stop after this many votes")
    parser.add_argument(
        "--recategorize",
        action="store_true",
        help="re-apply the category rules to stored votes; downloads nothing",
    )
    args = parser.parse_args()

    if args.recategorize:
        with connect() as conn:
            changed, unknown = recategorize(conn)
        print(f"{changed} votes changed category")
        for vote in unknown:
            print(
                f"  no rule for {vote['chamber']} question {vote['question']!r}: {vote['source_url']}"
            )
        return

    if args.chamber is None:
        parser.error("--chamber is required unless --recategorize is given")
    if (args.congress is None) != (args.session is None):
        parser.error("give both --congress and --session, or neither")

    with connect() as conn:
        if args.congress is None:
            checked = "the current session and the one before it"
            loaded = load_current(conn, args.chamber, date.today(), args.limit)
        else:
            checked = f"Congress {args.congress}, session {args.session}"
            loaded = load(conn, args.chamber, args.congress, args.session, args.limit)
    print(
        f"{loaded} {args.chamber} votes loaded from {SOURCES[args.chamber]} ({checked})"
    )


if __name__ == "__main__":
    main()
