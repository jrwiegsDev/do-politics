"""Load sitting members of Congress from congress-legislators into the officials table.

Usage:
    uv run --env-file .env python -m ingest.officials
    uv run --env-file .env python -m ingest.officials --dry-run
"""

import argparse
import json
import urllib.request
from datetime import date

from ingest.db import connect
from ingest.runs import record_run
from ingest.states import FIPS_BY_POSTAL, NON_VOTING

JOB = "officials"
SOURCE = "legislators-current"
LEGISLATORS_URL = f"https://unitedstates.github.io/congress-legislators/{SOURCE}.json"

# Congress has 541 seats and only a handful are ever vacant at once. A file
# with far fewer members is truncated or broken, and loading it would delete
# every member it leaves out.
MIN_EXPECTED_MEMBERS = 500

UPSERT_SQL = """
INSERT INTO officials (
    bioguide_id, name, party, chamber, state, state_fips, district_code,
    term_start, term_end, phone, office_address, website, contact_form, source
)
VALUES (
    %(bioguide_id)s, %(name)s, %(party)s, %(chamber)s, %(state)s, %(state_fips)s, %(district_code)s,
    %(term_start)s, %(term_end)s, %(phone)s, %(office_address)s, %(website)s, %(contact_form)s, %(source)s
)
ON CONFLICT (bioguide_id)
DO UPDATE SET
    name = EXCLUDED.name,
    party = EXCLUDED.party,
    chamber = EXCLUDED.chamber,
    state = EXCLUDED.state,
    state_fips = EXCLUDED.state_fips,
    district_code = EXCLUDED.district_code,
    term_start = EXCLUDED.term_start,
    term_end = EXCLUDED.term_end,
    phone = EXCLUDED.phone,
    office_address = EXCLUDED.office_address,
    website = EXCLUDED.website,
    contact_form = EXCLUDED.contact_form,
    source = EXCLUDED.source,
    updated_at = now();
"""

DELETE_DEPARTED_SQL = """
DELETE FROM officials
WHERE source = %(source)s
  AND bioguide_id <> ALL(%(bioguide_ids)s);
"""


def fetch() -> list[dict]:
    """Download the current members of Congress, one dict per member.

    Unlike the district boundaries, this is never reused from disk: members
    resign and are sworn in between runs, so every run reads the latest file.
    """
    with urllib.request.urlopen(LEGISLATORS_URL, timeout=30) as response:
        return json.load(response)


def district_code_for(state: str, district: int) -> str:
    """Return a House district number in the form districts.code uses."""
    if district == 0:
        return "98" if state in NON_VOTING else "00"

    return f"{district:02d}"


def to_row(member: dict) -> dict:
    """Turn one member record into one row for the officials table."""
    term = member["terms"][-1]
    name = member["name"]
    state = term["state"]

    if term["type"] == "sen":
        chamber = "senate"
        district_code = None
    else:
        chamber = "house"
        district_code = district_code_for(state, term["district"])

    return {
        "bioguide_id": member["id"]["bioguide"],
        "name": name.get("official_full") or f"{name['first']} {name['last']}",
        "party": term["party"],
        "chamber": chamber,
        "state": state,
        "state_fips": FIPS_BY_POSTAL[state],
        "district_code": district_code,
        "term_start": date.fromisoformat(term["start"]),
        "term_end": date.fromisoformat(term["end"]),
        "phone": term.get("phone"),
        "office_address": term.get("address"),
        "website": term.get("url"),
        "contact_form": term.get("contact_form"),
        "source": SOURCE,
    }


def read_officials() -> list[dict]:
    """Download and transform every member, refusing a file that is too short."""
    rows = [to_row(member) for member in fetch()]
    if len(rows) < MIN_EXPECTED_MEMBERS:
        raise ValueError(f"{SOURCE} has only {len(rows)} members; expected at least {MIN_EXPECTED_MEMBERS}")

    return rows


def load(conn) -> tuple[int, int]:
    """Upsert every member and remove the departed, as one recorded run.

    Returns how many members were loaded and how many were removed.
    """
    with record_run(conn, JOB, SOURCE) as run:
        rows = read_officials()
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
            cur.execute(DELETE_DEPARTED_SQL, {"source": SOURCE, "bioguide_ids": [row["bioguide_id"] for row in rows]})
            removed = cur.rowcount
        run.rows_loaded = len(rows)

    return len(rows), removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="download and print rows without touching the database")
    args = parser.parse_args()

    if args.dry_run:
        rows = read_officials()
        for row in rows:
            print(row["bioguide_id"], row["state"], row["district_code"] or "senate", row["name"])
        print(f"{len(rows)} officials read from {SOURCE}; nothing written")
        return

    with connect() as conn:
        loaded, removed = load(conn)
    print(f"{loaded} officials loaded from {SOURCE}, {removed} removed")


if __name__ == "__main__":
    main()
