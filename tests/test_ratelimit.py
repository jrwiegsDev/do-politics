"""Tests for the sliding-window rate limiter and the timer that forgets quiet visitors."""

import threading
import time
from collections import deque

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.deps import client_ip
from app.main import app
from app.ratelimit import RateLimiter, lookup_limiter


class FakeClock:
    """A clock the test moves by hand, so a minute passes instantly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(limit=3, window=60.0):
    clock = FakeClock()
    return RateLimiter(limit, window, clock=clock), clock


def test_hits_up_to_the_limit_are_allowed():
    limit, _ = limiter()

    assert [limit.check("a") for _ in range(3)] == [None, None, None]


def test_the_next_hit_is_refused_with_the_seconds_to_wait():
    limit, clock = limiter()
    limit.check("a")
    clock.now += 20
    limit.check("a")
    limit.check("a")

    # The first hit, 20 seconds ago, is the next to expire: 40 seconds left.
    assert limit.check("a") == 40.0


def test_a_hit_is_allowed_again_once_the_oldest_leaves_the_window():
    limit, clock = limiter()
    for _ in range(3):
        limit.check("a")

    clock.now += 60

    assert limit.check("a") is None


def test_the_window_slides_instead_of_resetting():
    limit, clock = limiter()
    limit.check("a")
    clock.now += 30
    limit.check("a")
    limit.check("a")

    # 31 seconds later only the first hit has left: one more is allowed,
    # then the two from 30 seconds earlier still count.
    clock.now += 31
    assert limit.check("a") is None
    assert limit.check("a") is not None


def test_a_refused_hit_is_not_counted():
    limit, clock = limiter()
    for _ in range(3):
        limit.check("a")
    for _ in range(5):
        assert limit.check("a") is not None

    clock.now += 60

    assert limit.check("a") is None


def test_each_visitor_is_counted_on_their_own():
    limit, _ = limiter()
    for _ in range(3):
        limit.check("a")

    assert limit.check("a") is not None
    assert limit.check("b") is None


class SlowToCount(deque):
    """A deque that pauses after being counted, so another thread can slip in.

    Without the pause, the gap between counting the hits and adding one is too
    short for threads to interleave, and the test would pass with or without
    the lock. Counting first and pausing after hands back a count that other
    threads may already have made stale, exactly the race the lock prevents.
    """

    def __len__(self) -> int:
        count = super().__len__()
        time.sleep(0.001)
        return count


def test_hits_at_the_same_moment_on_many_threads_never_pass_the_limit(monkeypatch):
    monkeypatch.setattr("app.ratelimit.deque", SlowToCount)
    limit = RateLimiter(10, 60.0)
    allowed = []
    start = threading.Barrier(50)

    def hit():
        start.wait()
        if limit.check("a") is None:
            allowed.append(1)

    threads = [threading.Thread(target=hit) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(allowed) == 10


def test_quiet_visitors_are_forgotten_and_recent_ones_kept():
    limit, clock = limiter()
    limit.check("quiet")
    clock.now += 45
    limit.check("recent")
    clock.now += 20

    limit.forget_quiet()

    assert set(limit.hits) == {"recent"}


def test_clear_forgets_everyone():
    limit, _ = limiter()
    limit.check("a")

    limit.clear()

    assert limit.hits == {}


def test_the_app_forgets_quiet_visitors_on_a_timer(database, monkeypatch):
    # Every few seconds while the app runs, without waiting for another visit.
    sweeps = []
    monkeypatch.setattr("app.deps.SWEEP_SECONDS", 0.01)
    monkeypatch.setattr(lookup_limiter, "forget_quiet", lambda: sweeps.append(1))

    with TestClient(app):
        deadline = time.monotonic() + 2
        while len(sweeps) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert len(sweeps) >= 3


def request_with(headers=(), client=("10.0.0.1", 50000)):
    """Build a bare request with these headers, arriving from this connection."""
    scope = {
        "type": "http",
        "headers": [(name.encode(), value.encode()) for name, value in headers],
        "client": client,
    }
    return Request(scope)


def test_the_visitor_is_cloudflare_s_connecting_ip():
    request = request_with([("cf-connecting-ip", "8.8.4.4")])

    assert client_ip(request) == "8.8.4.4"


def test_a_faked_forwarded_for_is_ignored():
    # Render keeps what the visitor wrote at the start of X-Forwarded-For.
    request = request_with(
        [
            ("x-forwarded-for", "203.0.113.7, 8.8.4.4, 162.158.1.1"),
            ("cf-connecting-ip", "8.8.4.4"),
        ]
    )

    assert client_ip(request) == "8.8.4.4"


@pytest.mark.parametrize("sent", ["", "not-an-ip", "8.8.4.4, 1.2.3.4"])
def test_without_a_valid_cloudflare_header_the_connection_is_used(sent):
    request = request_with([("cf-connecting-ip", sent)] if sent else [])

    assert client_ip(request) == "10.0.0.1"
