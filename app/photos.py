"""Find a member's stored photo, and tell whether a browser already has it."""

import psycopg

FIND_PHOTO_SQL = """
SELECT image, sha256
FROM member_photos
WHERE bioguide_id = %(bioguide_id)s;
"""


def find_photo(conn: psycopg.Connection, bioguide_id: str) -> tuple[bytes, str] | None:
    """Return a member's photo and its SHA-256 fingerprint, or None if none is stored."""
    row = conn.execute(FIND_PHOTO_SQL, {"bioguide_id": bioguide_id}).fetchone()
    if row is None:
        return None
    image, sha256 = row
    return bytes(image), sha256


def etag_for(sha256: str) -> str:
    """Return the ETag for a photo. HTTP writes an ETag inside double quotes."""
    return f'"{sha256}"'


def etag_matches(if_none_match: str | None, sha256: str) -> bool:
    """Say whether a browser's If-None-Match header names this photo.

    The header lists the ETags of the copies the browser holds. It can name
    several, separated by commas, mark one as weak with W/, or be * for any.
    """
    if if_none_match is None:
        return False
    if if_none_match.strip() == "*":
        return True
    held = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    return etag_for(sha256) in held
