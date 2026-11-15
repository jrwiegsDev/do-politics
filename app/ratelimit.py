"""Count each visitor's address lookups and refuse any over the limit.

Every lookup asks the Census Geocoder, a free public service, for the
address's location. A script sending thousands of lookups through this site
could get the site blocked by the Census, which would break it for everyone,
so each visitor gets a few lookups a minute.

Visitors are told apart by IP address. An IP address is held only in this
program's memory, only while that visitor has lookups in the last window,
and is never written to the database or logs, as the privacy policy says.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

# How many lookups one visitor may make in any WINDOW_SECONDS stretch.
LOOKUP_LIMIT = 10
WINDOW_SECONDS = 60.0


class RateLimiter:
    """A sliding-window limit: at most `limit` hits per key in any `window` seconds."""

    def __init__(
        self,
        limit: int,
        window: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self.hits: dict[str, deque[float]] = {}
        # Routes run on many threads at once, so only one at a time may
        # read and change the counts.
        self.lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record a hit for key and return None, or return the seconds to wait.

        A refused hit is not recorded, so waiting is always enough to be let
        back in.
        """
        now = self.clock()
        with self.lock:
            times = self.hits.setdefault(key, deque())
            while times and times[0] <= now - self.window:
                times.popleft()
            if len(times) >= self.limit:
                return times[0] + self.window - now
            times.append(now)
            return None

    def forget_quiet(self) -> None:
        """Forget every key with no hit in the last window."""
        cutoff = self.clock() - self.window
        with self.lock:
            self.hits = {
                key: times
                for key, times in self.hits.items()
                if times and times[-1] > cutoff
            }

    def clear(self) -> None:
        """Forget every key. The tests use this so each one starts with no hits."""
        with self.lock:
            self.hits.clear()


lookup_limiter = RateLimiter(LOOKUP_LIMIT, WINDOW_SECONDS)
