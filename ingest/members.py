"""Load everyone who has served in the 119th Congress into the members table.

Usage:
    uv run --env-file .env python -m ingest.members
    uv run --env-file .env python -m ingest.members --dry-run
"""

import argparse
import json
import urllib.request
from datetime import date

from ingest.db import connect
from ingest.runs import record_run

JOB = "members"
PROJECT = "congress-legislators"
# Sitting members are in the first file. Anyone who has left office, this
# Congress or any earlier one, is in the second.
SOURCES = ("legislators-current", "legislators-historical")

# The 119th Congress convened on this day. A term that ended on or before it
# belongs to an earlier Congress.
CONGRESS_START = date(2025, 1, 3)

# Congress has 541 seats, so far fewer people than that means a file is
# truncated or broken. Nothing is deleted here, but the votes loader relies
# on this table being complete, so a short read fails the run instead.
MIN_EXPECTED_MEMBERS = 500

UPSERT_SQL = """
INSERT INTO members (bioguide_id, lis_id, name, source)
VALUES (%(bioguide_id)s, %(lis_id)s, %(name)s, %(source)s)
ON CONFLICT (bioguide_id)
DO UPDATE SET
    lis_id = EXCLUDED.lis_id,
    name = EXCLUDED.name,
    source = EXCLUDED.source,
    updated_at = now();
"""


def fetch(source: str) -> list[dict]:
    """Download one congress-legislators file, one dict per person.

    Never reused from disk, for the same reason as the officials loader:
    people move from the current file to the historical one between runs.
    """
    url = f"https://unitedstates.github.io/{PROJECT}/{source}.json"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def served_this_congress(member: dict) -> bool:
    """True when any of the person's terms ran past the start of this Congress."""
    return any(date.fromisoformat(term["end"]) > CONGRESS_START for term in member["terms"])


def to_row(member: dict, source: str) -> dict:
    """Turn one person's record into one row for the members table."""
    name = member["name"]

    return {
        "bioguide_id": member["id"]["bioguide"],
        "lis_id": member["id"].get("lis"),
        "name": name.get("official_full") or f"{name['first']} {name['last']}",
        "source": source,
    }


def read_members() -> list[dict]:
    """Download both files and transform everyone who served in this Congress."""
    rows = []
    for source in SOURCES:
        rows.extend(to_row(member, source) for member in fetch(source) if served_this_congress(member))

    if len(rows) < MIN_EXPECTED_MEMBERS:
        raise ValueError(f"{PROJECT} has only {len(rows)} members; expected at least {MIN_EXPECTED_MEMBERS}")

    return rows


def load(conn) -> int:
    """Upsert every member as one recorded run, and return how many were loaded.

    Nobody is ever removed: a member's votes outlast their time in office.
    """
    with record_run(conn, JOB, PROJECT) as run:
        rows = read_members()
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        run.rows_loaded = len(rows)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="download and print rows without touching the database")
    args = parser.parse_args()

    if args.dry_run:
        rows = read_members()
        for row in rows:
            print(row["bioguide_id"], row["lis_id"] or "-", row["source"], row["name"])
        print(f"{len(rows)} members read from {PROJECT}; nothing written")
        return

    with connect() as conn:
        loaded = load(conn)
    print(f"{loaded} members loaded from {PROJECT}")


if __name__ == "__main__":
    main()
