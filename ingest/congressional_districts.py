"""Load congressional district boundaries from Census TIGER/Line into the districts table.

Usage:
    uv run --env-file .env python -m ingest.congressional_districts --state 17
"""

import argparse
import json
import os
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import psycopg
import shapefile

DATA_DIR = Path("data/tiger")
TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER{vintage}/CD/{name}.zip"

# The Census marks water and other areas outside any district with this code.
NOT_A_DISTRICT = "ZZ"

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
        urllib.request.urlretrieve(TIGER_URL.format(vintage=vintage, name=name), archive)
    if not extracted.exists():
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extracted)

    return extracted / f"{name}.shp"


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


def connect() -> psycopg.Connection:
    """Connect with DATABASE_URL if set, otherwise with the local Compose settings."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg.connect(url)

    return psycopg.connect(
        host="127.0.0.1",
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", required=True, help="two-digit state FIPS code, e.g. 17 for Illinois")
    parser.add_argument("--vintage", type=int, default=2025, help="TIGER/Line release year")
    parser.add_argument("--congress", type=int, default=119, help="Congress the map belongs to")
    parser.add_argument(
        "--effective-from",
        type=date.fromisoformat,
        default=date(2023, 1, 3),
        help="date the map took effect (YYYY-MM-DD)",
    )
    parser.add_argument("--dry-run", action="store_true", help="read the file and print rows without touching the database")
    args = parser.parse_args()

    name = f"tl_{args.vintage}_{args.state}_cd{args.congress}"
    rows = list(read_districts(fetch(name, args.vintage), args.congress, args.effective_from))

    if args.dry_run:
        for row in rows:
            print(row["geoid"], row["code"], row["name"], f"({len(row['boundary']):,} bytes of GeoJSON)")
        print(f"{len(rows)} districts read from {name}; nothing written")
        return

    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)

    print(f"{len(rows)} districts loaded from {name}")


if __name__ == "__main__":
    main()
