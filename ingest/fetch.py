"""Open web addresses for the loaders, trying again when a failure is likely to pass."""

import random
import time
import urllib.error
import urllib.request
from http.client import HTTPResponse

# Statuses worth asking again for. 429 means "too many requests, slow down",
# and the 5xx codes are the server's own failures. 403 normally means "not
# allowed", but senate.gov sends it now and then for an address it serves
# again a minute later.
RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})

ATTEMPTS = 4
BASE_DELAY_SECONDS = 1.0


def backoff_delay(attempt: int) -> float:
    """Return how long to wait after a failed attempt: about 1, 2, then 4 seconds.

    Each wait doubles, plus up to a second of random jitter, so that many
    clients that failed together don't all try again at the same moment.
    """
    return BASE_DELAY_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 1)


def open_url(request: urllib.request.Request | str, timeout: float) -> HTTPResponse:
    """Open a URL, trying up to ATTEMPTS times before giving up.

    A status not in RETRY_STATUSES, such as 404, is raised at once, since
    asking again would get the same answer. The last attempt sits outside
    the loop, so its failure is raised to the caller as it is.
    """
    for attempt in range(1, ATTEMPTS):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUSES:
                raise
            error.close()
        except (urllib.error.URLError, TimeoutError):
            # A dropped connection or a timeout: the network, not the address.
            pass
        time.sleep(backoff_delay(attempt))
    return urllib.request.urlopen(request, timeout=timeout)
