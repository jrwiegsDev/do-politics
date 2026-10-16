"""Load a photo of every sitting member of Congress into member_photos.

Run ingest.officials first: the members to load are the ones holding a seat.
Each photo comes from the unitedstates/images project when it has one, and
otherwise from the member's record on Congress.gov, which needs the key in
CONGRESS_API_KEY.

Usage:
    uv run --env-file .env python -m ingest.photos
    uv run --env-file .env python -m ingest.photos --limit 3

The photos are kept in the database rather than in a folder, because a
Render server's disk is replaced on every deploy.
"""

import argparse
import hashlib
import urllib.error
import urllib.request
from itertools import islice
from time import sleep

from ingest.bills import fetch_json
from ingest.db import connect
from ingest.plain_text import html_to_text
from ingest.runs import record_run
from ingest.user_agent import USER_AGENT

JOB = "photos"
SOURCE = "unitedstates/images, congress.gov"
PAUSE_SECONDS = 0.1

# 225 by 275 pixels, about 47 KB: sharp at twice the size the page shows it.
UNITEDSTATES_URL = (
    "https://raw.githubusercontent.com/unitedstates/images/gh-pages/"
    "congress/225x275/{bioguide_id}.jpg"
)

# Every JPEG file starts with these three bytes.
JPEG_SIGNATURE = b"\xff\xd8\xff"

# The table refuses a larger image; this stops the download before that.
MAX_BYTES = 1_000_000

SITTING_MEMBERS_SQL = """
SELECT bioguide_id FROM officials ORDER BY bioguide_id;
"""

# A photo is rewritten only when its image or its credit has changed, so
# updated_at says when the photo itself last changed.
UPSERT_SQL = """
INSERT INTO member_photos (
    bioguide_id, image, sha256, source, source_url, attribution
)
VALUES (
    %(bioguide_id)s, %(image)s, %(sha256)s, %(source)s, %(source_url)s, %(attribution)s
)
ON CONFLICT (bioguide_id)
DO UPDATE SET
    image = EXCLUDED.image,
    sha256 = EXCLUDED.sha256,
    source = EXCLUDED.source,
    source_url = EXCLUDED.source_url,
    attribution = EXCLUDED.attribution,
    updated_at = now()
WHERE member_photos.sha256 <> EXCLUDED.sha256
   OR member_photos.attribution IS DISTINCT FROM EXCLUDED.attribution;
"""


def check_image(data: bytes) -> None:
    """Raise ValueError unless data looks like a JPEG small enough to store."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"image is over {MAX_BYTES} bytes")
    if not data.startswith(JPEG_SIGNATURE):
        raise ValueError("image is not a JPEG")


def download(url: str) -> bytes | None:
    """Download one image, or return None when the site has no file there."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            # One byte more than allowed, so an image that is too large is
            # caught without reading the rest of it.
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise

    check_image(data)
    return data


def to_row(
    bioguide_id: str,
    image: bytes,
    source: str,
    source_url: str,
    attribution: str | None,
) -> dict:
    """Build one member_photos row. The fingerprint is worked out from the image."""
    return {
        "bioguide_id": bioguide_id,
        "image": image,
        "sha256": hashlib.sha256(image).hexdigest(),
        "source": source,
        "source_url": source_url,
        "attribution": attribution,
    }


def find_photo(bioguide_id: str) -> dict | None:
    """Return a member_photos row for one member, or None when no source has a photo.

    unitedstates/images is asked first: its photos are public domain and
    larger. Congress.gov fills the gaps, mostly members who arrived in a
    special election, and names whom to credit for each photo.
    """
    url = UNITEDSTATES_URL.format(bioguide_id=bioguide_id)
    image = download(url)
    if image is not None:
        return to_row(bioguide_id, image, "unitedstates", url, None)

    try:
        member = fetch_json(f"member/{bioguide_id}")["member"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise

    depiction = member.get("depiction") or {}
    url = depiction.get("imageUrl")
    if not url:
        return None

    image = download(url)
    if image is None:
        return None

    # The credit arrives as a link, such as '<a href="...">Courtesy U.S.
    # Senate Historical Office</a>'. Only its words are kept.
    credit = html_to_text(depiction.get("attribution") or "") or None
    return to_row(bioguide_id, image, "congress.gov", url, credit)


def load(conn, limit: int | None = None) -> tuple[int, int, list[str]]:
    """Load a photo for every sitting member, as one recorded run.

    Returns how many photos were new or changed, how many were already
    stored as they are, and the members no source has a photo of. Each photo
    is committed as soon as it is saved, so a run that fails partway keeps
    the photos already saved.
    """
    with record_run(conn, JOB, SOURCE) as run:
        sitting = [row[0] for row in conn.execute(SITTING_MEMBERS_SQL)]
        unchanged = 0
        missing = []

        for bioguide_id in islice(sitting, limit):
            row = find_photo(bioguide_id)
            if row is None:
                missing.append(bioguide_id)
                continue

            # The upsert changes nothing when the stored photo is the same,
            # and then the database reports no rows written.
            written = conn.execute(UPSERT_SQL, row).rowcount
            conn.commit()
            if written:
                run.rows_loaded += 1
                print(f"{bioguide_id}: saved from {row['source']}", flush=True)
            else:
                unchanged += 1
            sleep(PAUSE_SECONDS)

    return run.rows_loaded, unchanged, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="stop after this many members")
    args = parser.parse_args()

    with connect() as conn:
        saved, unchanged, missing = load(conn, args.limit)
    print(f"{saved} photos new or changed, {unchanged} unchanged")
    for bioguide_id in missing:
        print(f"  no photo found: {bioguide_id}")


if __name__ == "__main__":
    main()
