"""Made-up officials, members and votes shared by the tests."""

from datetime import date, datetime, timezone

from ingest.members import UPSERT_SQL as UPSERT_MEMBER_SQL
from ingest.officials import UPSERT_SQL
from ingest.votes import save


def official(bioguide_id, name, chamber, district_code=None, state="IL", state_fips="17"):
    """Build a made-up officials row with a term from 2025-01-03 to 2027-01-03."""
    return {
        "bioguide_id": bioguide_id,
        "name": name,
        "party": "Independent",
        "chamber": chamber,
        "state": state,
        "state_fips": state_fips,
        "district_code": district_code,
        "term_start": date(2025, 1, 3),
        "term_end": date(2027, 1, 3),
        "phone": None,
        "office_address": None,
        "website": None,
        "contact_form": None,
        "source": "test",
    }


SENATOR_A = official("T000010", "Alex Senator", "senate")
SENATOR_B = official("T000011", "Blake Senator", "senate")
REP_13 = official("T000012", "Casey Rep", "house", "13")
REP_12 = official("T000013", "Drew Rep", "house", "12")
MISSOURI_SENATOR = official("T000014", "Eli Senator", "senate", state="MO", state_fips="29")


def add(conn, *rows):
    """Insert made-up officials rows."""
    for row in rows:
        conn.execute(UPSERT_SQL, row)


def names(officials):
    """Return just the names, in the order the lookup gave them."""
    return [o["name"] for o in officials]


MEMBERS = [
    {"bioguide_id": "T000001", "lis_id": "S900", "name": "Pat Example", "source": "legislators-current"},
    {"bioguide_id": "T000002", "lis_id": "S901", "name": "Sam Sample", "source": "legislators-current"},
]


def add_members(conn) -> None:
    """Insert the two made-up members the sample votes refer to."""
    for member in MEMBERS:
        conn.execute(UPSERT_MEMBER_SQL, member)


def add_vote(conn, roll_number, day, positions, question="On Passage", chamber="house", title="Example Act"):
    """Store a made-up vote held at noon UTC on the given day of January 2026.

    positions maps a bioguide ID to how that member voted, such as
    {"T000001": "Yea"}. The vote's category is worked out from its question,
    exactly as the loader does it.
    """
    vote = {
        "chamber": chamber,
        "congress": 119,
        "session": 2,
        "roll_number": roll_number,
        "voted_at": datetime(2026, 1, day, 12, 0, tzinfo=timezone.utc),
        "question": question,
        "vote_type": None,
        "issue": "H R 9999",
        "title": title,
        "result": "Passed",
        "source_url": f"https://example.test/{chamber}/{roll_number}",
    }
    rows = [{"bioguide_id": b, "position": p, "party": "I", "state": "IL"} for b, p in positions.items()]
    save(conn, vote, rows)
