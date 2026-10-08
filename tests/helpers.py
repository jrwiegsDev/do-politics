"""Made-up officials shared by the tests."""

from datetime import date

from ingest.officials import UPSERT_SQL


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
