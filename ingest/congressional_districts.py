"""Load congressional district boundaries from Census TIGER/Line into the districts table.

Usage:
    uv run --env-file .env python -m ingest.congressional_districts --state 17
    uv run --env-file .env python -m ingest.congressional_districts --state all
"""

import argparse
import json
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import shapefile

from ingest.db import connect
from ingest.runs import record_run

JOB = "congressional_districts"
DATA_DIR = Path("data/tiger")
TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER{vintage}/CD/{name}.zip"

# FIPS codes of everything the Census publishes a congressional district file
# for: the 50 states, DC (11), and the five territories (60 American Samoa,
# 66 Guam, 69 Northern Mariana Islands, 72 Puerto Rico, 78 Virgin Islands).
ALL_STATES = (
    "01",
    "02",
    "04",
    "05",
    "06",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "38",
    "39",
    "40",
    "41",
    "42",
    "44",
    "45",
    "46",
    "47",
    "48",
    "49",
    "50",
    "51",
    "53",
    "54",
    "55",
    "56",
    "60",
    "66",
    "69",
    "72",
    "78",
)

# The Census marks water and other areas outside any district with this code.
NOT_A_DISTRICT = "ZZ"

# A map takes effect the day the first Congress elected under it is seated.
# Most states have used one map since the 118th Congress (seated 2023-01-03).
# Alabama, Georgia, Louisiana, New York and North Carolina redrew before the
# 2024 election, so their current maps date from the 119th (seated 2025-01-03).
REDRAWN_FOR_119TH = {"01", "13", "22", "36", "37"}

UPSERT_SQL = """
INSERT INTO districts (level, chamber, state_fips, code, geoid, name, effective_from, source, boundary)
VALUES (%(level)s, %(chamber)s, %(state_fips)s, %(code)s, %(geoid)s, %(name)s, %(effective_from)s, %(source)s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(boundary)s), 4269)))
ON CONFLICT (level, chamber, geoid, effective_from)
DO UPDATE SET
    name = EXCLUDED.name,
    source = EXCLUDED.source,
    boundary = EXCLUDED.boundary;
"""


def fetch(name: str, vintage: int) -> Path:
    """Download and unzip one TIGER/Line file, reusing a copy already on disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive = DATA_DIR / f"{name}.zip"
    extracted = DATA_DIR / name

    if not archive.exists():
        # Download under a temporary name so an interrupted download is never
        # mistaken for a complete file on the next run.
        partial = archive.with_suffix(".part")
        urllib.request.urlretrieve(
            TIGER_URL.format(vintage=vintage, name=name), partial
        )
        partial.rename(archive)
    if not extracted.exists():
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extracted)

    return extracted / f"{name}.shp"


def effective_from_for(state: str) -> date:
    """Return the date the state's 119th Congress map took effect."""
    return date(2025, 1, 3) if state in REDRAWN_FOR_119TH else date(2023, 1, 3)


def read_districts(shp_path: Path, congress: int, effective_from: date):
    """Yield one dict per district, with keys matching the districts table columns."""
    with shapefile.Reader(str(shp_path)) as reader:
        for item in reader.iterShapeRecords():
            record = item.record.as_dict()
            code = record[f"CD{congress}FP"]
            if code == NOT_A_DISTRICT:
                continue

            yield {
                "level": "federal",
                "chamber": "house",
                "state_fips": record["STATEFP"],
                "code": code,
                "geoid": record["GEOID"],
                "name": record["NAMELSAD"],
                "effective_from": effective_from,
                "source": shp_path.stem,
                "boundary": json.dumps(item.shape.__geo_interface__),
            }


def load_state(
    conn, name: str, vintage: int, congress: int, effective_from: date
) -> int:
    """Download, read and upsert one state's districts as one recorded run."""
    with record_run(conn, JOB, name) as run:
        rows = list(read_districts(fetch(name, vintage), congress, effective_from))
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        run.rows_loaded = len(rows)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--state",
        required=True,
        help="two-digit state FIPS code, e.g. 17 for Illinois, or 'all'",
    )
    parser.add_argument(
        "--vintage", type=int, default=2025, help="TIGER/Line release year"
    )
    parser.add_argument(
        "--congress", type=int, default=119, help="Congress the map belongs to"
    )
    parser.add_argument(
        "--effective-from",
        type=date.fromisoformat,
        help="date the map took effect (YYYY-MM-DD); looked up by state for the 119th Congress",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the file and print rows without touching the database",
    )
    args = parser.parse_args()

    if args.effective_from is None and args.congress != 119:
        parser.error(
            "--effective-from is required for any Congress other than the 119th"
        )
    if args.state != "all" and args.state not in ALL_STATES:
        parser.error(f"unknown state FIPS code: {args.state}")
    states = ALL_STATES if args.state == "all" else (args.state,)

    def name_for(state: str) -> str:
        return f"tl_{args.vintage}_{state}_cd{args.congress}"

    def effective_from(state: str) -> date:
        return args.effective_from or effective_from_for(state)

    if args.dry_run:
        for state in states:
            name = name_for(state)
            rows = list(
                read_districts(
                    fetch(name, args.vintage), args.congress, effective_from(state)
                )
            )
            for row in rows:
                print(
                    row["geoid"],
                    row["code"],
                    row["name"],
                    f"({len(row['boundary']):,} bytes of GeoJSON)",
                )
            print(f"{len(rows)} districts read from {name}; nothing written")
        return

    # One state failing (a bad download, say) should not stop the rest. Each
    # state is its own run and its own transaction. That is why every kind of
    # error is caught here, which the linter would otherwise flag.
    failed = []
    with connect() as conn:
        for state in states:
            name = name_for(state)
            try:
                count = load_state(
                    conn, name, args.vintage, args.congress, effective_from(state)
                )
            except Exception as exc:  # noqa: BLE001
                failed.append(state)
                print(f"FAILED {name}: {exc}")
                continue
            print(f"{count} districts loaded from {name}")

    if failed:
        sys.exit(f"{len(failed)} of {len(states)} failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
