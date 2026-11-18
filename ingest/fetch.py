"""Download whole answers for the loaders, trying again when a failure is likely to pass.

One attempt is the whole download: connecting, the status and headers, and
reading the body to the end. A server that stalls or hangs up halfway
through the body is tried again like one that never answered.

Each retry is logged as a warning on the ingest.fetch logger, with the
address, what went wrong and how long the wait is, so a run that recovered
from failures can be told apart from one that was only slow. The line never
holds a request's headers, where the Congress.gov API key travels.
"""

import http.client
import logging
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# Statuses worth asking again for. 429 means "too many requests, slow down",
# and the 5xx codes are the server's own failures. 403 normally means "not
# allowed", but senate.gov sends it now and then for an address it serves
# again a minute later.
RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})

# Failures of the network, not the address. URLError: no connection.
# TimeoutError: the server went quiet, before the headers or partway through
# the body. ConnectionError: it hung up. IncompleteRead: the body ended
# before the length its headers promised.
NETWORK_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.IncompleteRead,
)

ATTEMPTS = 4
BASE_DELAY_SECONDS = 1.0

# A helper logs; each loader's main decides where the lines go.
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Download:
    """A whole answer: the address it came from, after any redirect, and its body."""

    url: str
    body: bytes


def backoff_delay(attempt: int) -> float:
    """Return how long to wait after a failed attempt: about 1, 2, then 4 seconds.

    Each wait doubles, plus up to a second of random jitter, so that many
    clients that failed together don't all try again at the same moment.
    """
    return BASE_DELAY_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 1)


def address(request: urllib.request.Request | str) -> str:
    """Return the address a request asks for, without its headers."""
    return request if isinstance(request, str) else request.full_url


def read_once(
    request: urllib.request.Request | str, timeout: float, max_bytes: int | None
) -> Download:
    """Make one attempt: connect, read the headers, then read the body to the end.

    With max_bytes, one byte more than allowed is read, so a body that is
    too large is caught without reading the rest of it.
    """
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if max_bytes is None:
            body = response.read()
        else:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError(f"{address(request)} is over {max_bytes} bytes")
        return Download(response.url, body)


def read_url(
    request: urllib.request.Request | str,
    timeout: float,
    max_bytes: int | None = None,
) -> Download:
    """Download a URL whole, trying up to ATTEMPTS times before giving up.

    A status not in RETRY_STATUSES, such as 404, is raised at once, since
    asking again would get the same answer, and so is a body over max_bytes.
    The last attempt sits outside the loop, so its failure is raised to the
    caller as it is.
    """
    for attempt in range(1, ATTEMPTS):
        try:
            return read_once(request, timeout, max_bytes)
        except urllib.error.HTTPError as error:
            # Checked first: an HTTPError is also a URLError.
            if error.code not in RETRY_STATUSES:
                raise
            error.close()
            failure = f"HTTP {error.code}"
        except NETWORK_ERRORS as error:
            failure = type(error).__name__
        delay = backoff_delay(attempt)
        logger.warning(
            "%s failed with %s; trying again in %.1f s (attempt %d of %d)",
            address(request),
            failure,
            delay,
            attempt,
            ATTEMPTS,
        )
        time.sleep(delay)
    return read_once(request, timeout, max_bytes)
