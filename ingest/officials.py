"""Load sitting members of Congress, and every term each has served, from congress-legislators.

Usage:
    uv run --env-file .env python -m ingest.officials
    uv run --env-file .env python -m ingest.officials --dry-run
"""

import argparse
import json
from datetime import date

from ingest.congress import today_in_washington
from ingest.db import connect
from ingest.fetch import read_url
from ingest.logs import configure_logging
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
    term_start, term_end, phone, office_address, website, contact_form,
    leadership_titles, source
)
VALUES (
    %(bioguide_id)s, %(name)s, %(party)s, %(chamber)s, %(state)s, %(state_fips)s, %(district_code)s,
    %(term_start)s, %(term_end)s, %(phone)s, %(office_address)s, %(website)s, %(contact_form)s,
    %(leadership_titles)s, %(source)s
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
    leadership_titles = EXCLUDED.leadership_titles,
    source = EXCLUDED.source,
    updated_at = now();
"""

# A member's terms are replaced as a set: the file's list is the whole
# history, so rows from an earlier run are deleted, not merged.
DELETE_TERMS_SQL = """
DELETE FROM official_terms
WHERE bioguide_id = ANY(%(bioguide_ids)s);
"""

INSERT_TERM_SQL = """
INSERT INTO official_terms (
    bioguide_id, position, chamber, state, district, party, starts_on, ends_on, how
)
VALUES (
    %(bioguide_id)s, %(position)s, %(chamber)s, %(state)s, %(district)s, %(party)s,
    %(starts_on)s, %(ends_on)s, %(how)s
);
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
    return json.loads(read_url(LEGISLATORS_URL, timeout=30).body)


def district_code_for(state: str, district: int) -> str:
    """Return a House district number in the form districts.code uses."""
    if district == 0:
        return "98" if state in NON_VOTING else "00"

    return f"{district:02d}"


def current_leadership_titles(member: dict, on_date: date) -> list[str]:
    """Return the titles of the leadership roles a member holds on a date.

    A role still held has no end date. One that has ended, or has not
    started yet, is left out.
    """
    return [
        role["title"]
        for role in member.get("leadership_roles", [])
        if date.fromisoformat(role["start"]) <= on_date
        and ("end" not in role or date.fromisoformat(role["end"]) > on_date)
    ]


def to_row(member: dict, on_date: date | None = None) -> dict:
    """Turn one member record into one row for the officials table.

    on_date decides which leadership roles are current; it is today unless
    a test names another day.
    """
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
        "leadership_titles": current_leadership_titles(
            member, on_date or today_in_washington()
        ),
        "source": SOURCE,
    }


def to_terms(member: dict) -> list[dict]:
    """Turn one member record's terms into rows for official_terms, oldest first.

    A partial term reached by appointment or special election is its own
    row, as in the file; how says which, when the file does.
    """
    bioguide_id = member["id"]["bioguide"]
    return [
        {
            "bioguide_id": bioguide_id,
            "position": position,
            "chamber": "senate" if term["type"] == "sen" else "house",
            "state": term["state"],
            "district": term["district"] if term["type"] == "rep" else None,
            "party": term["party"],
            "starts_on": date.fromisoformat(term["start"]),
            "ends_on": date.fromisoformat(term["end"]),
            "how": term.get("how"),
        }
        for position, term in enumerate(member["terms"])
    ]


def read_officials() -> tuple[list[dict], list[dict]]:
    """Download and transform every member and their terms.

    Refuses a file that is too short, before anything is written.
    """
    members = fetch()
    rows = [to_row(member) for member in members]
    if len(rows) < MIN_EXPECTED_MEMBERS:
        raise ValueError(
            f"{SOURCE} has only {len(rows)} members; expected at least {MIN_EXPECTED_MEMBERS}"
        )

    terms = [term for member in members for term in to_terms(member)]
    return rows, terms


def load(conn) -> tuple[int, int, int]:
    """Upsert every member, replace their terms and remove the departed, as one run.

    It is one transaction, so the officials and their terms never disagree.
    A departed member's terms go with them (ON DELETE CASCADE). Returns how
    many members were loaded, how many terms, and how many members were
    removed.
    """
    with record_run(conn, JOB, SOURCE) as run:
        rows, terms = read_officials()
        bioguide_ids = [row["bioguide_id"] for row in rows]
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
            cur.execute(
                DELETE_DEPARTED_SQL, {"source": SOURCE, "bioguide_ids": bioguide_ids}
            )
            removed = cur.rowcount
            cur.execute(DELETE_TERMS_SQL, {"bioguide_ids": bioguide_ids})
            cur.executemany(INSERT_TERM_SQL, terms)
        run.rows_loaded = len(rows)

    return len(rows), len(terms), removed


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="download and print rows without touching the database",
    )
    args = parser.parse_args()

    if args.dry_run:
        rows, terms = read_officials()
        for row in rows:
            print(
                row["bioguide_id"],
                row["state"],
                row["district_code"] or "senate",
                row["name"],
            )
        print(
            f"{len(rows)} officials and {len(terms)} terms read from {SOURCE};"
            " nothing written"
        )
        return

    with connect() as conn:
        loaded, terms, removed = load(conn)
    print(f"{loaded} officials loaded from {SOURCE}, {removed} removed")
    print(f"{terms} terms loaded for them")


if __name__ == "__main__":
    main()
