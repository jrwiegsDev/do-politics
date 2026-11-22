"""Static file addresses that change whenever the file does.

A page links each static file with a fingerprint of its contents, such as
/static/style.css?v=3f9a1c2b7d4e. A browser may keep that address for a
year without asking again, because a changed file gets a new fingerprint
and so a new address, which no browser has kept. Without this, a browser
guesses how long its copy is good, and after a deploy a returning visitor
could get the new page with the old stylesheet.

An address with no fingerprint, or an out-of-date one, is still answered,
but the browser is told to check back before using its copy again.
"""

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIR = Path(__file__).parent / "static"

# A year, the longest a browser is asked to keep anything. immutable tells it
# not to ask again even when the visitor reloads the page.
KEEP_FOR_A_YEAR = "public, max-age=31536000, immutable"
# Keep a copy, but ask the server first; an unchanged file costs a short
# "304 Not Modified" answer with no body.
CHECK_FIRST = "no-cache"


@lru_cache(maxsize=64)
def _fingerprint(name: str, modified_ns: int, size: int) -> str:
    """Hash a file's contents. The cache key changes whenever the file is saved."""
    contents = (STATIC_DIR / name).read_bytes()
    return hashlib.sha256(contents).hexdigest()[:12]


def fingerprint(name: str) -> str:
    """Return the first 12 hex digits of the SHA-256 of a static file.

    The file's modified time and size are part of the cache key, so an edit
    on a laptop gets a new fingerprint at once with no restart, while
    unchanged files are read only once.
    """
    stat = (STATIC_DIR / name).stat()
    return _fingerprint(name, stat.st_mtime_ns, stat.st_size)


def static_url(name: str) -> str:
    """Return the address a page uses for a static file, fingerprint included.

    Templates use it as a filter: {{ "style.css" | static_url }}.
    """
    return f"/static/{name}?v={fingerprint(name)}"


class FingerprintedStaticFiles(StaticFiles):
    """Serve the static folder, telling browsers how long they may keep each file."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        name = Path(full_path).resolve().relative_to(STATIC_DIR.resolve()).as_posix()
        asked_for = parse_qs(scope["query_string"].decode()).get("v", [""])[0]
        current = asked_for == fingerprint(name)
        response.headers["Cache-Control"] = KEEP_FOR_A_YEAR if current else CHECK_FIRST
        return response
