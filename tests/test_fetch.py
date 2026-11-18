"""Tests for read_url: which failures are tried again, how many times, and how long it waits."""

import http.client
import io
import logging
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from ingest.fetch import ATTEMPTS, Download, backoff_delay, read_url
from ingest.logs import LOG_FORMAT, configure_logging
from ingest.votes import fetch_xml

URL = "https://example.test/vote.xml"


class FakeResponse(io.BytesIO):
    """Stands in for what urlopen returns: a file to read, at the address asked for."""

    url = URL


class StallingResponse(FakeResponse):
    """Headers arrived, then the body failed partway, as on 2026-11-18.

    Remembers whether it was closed, so a test can check the half-read
    connection was let go before the next attempt.
    """

    def __init__(self, error: BaseException):
        super().__init__(b"")
        self.error = error
        self.closed_by_caller = False

    def read(self, size=-1):
        raise self.error

    def close(self):
        self.closed_by_caller = True
        super().close()


class TrackedHTTPError(urllib.error.HTTPError):
    """An HTTPError that remembers whether it was closed."""

    closed_by_caller = False

    def close(self):
        self.closed_by_caller = True
        super().close()


def http_error(code: int) -> TrackedHTTPError:
    return TrackedHTTPError(URL, code, "error", Message(), None)


@pytest.fixture
def waits(monkeypatch):
    """Record each attempt number read_url waits after, instead of waiting."""
    seen = []
    monkeypatch.setattr(
        "ingest.fetch.backoff_delay", lambda attempt: seen.append(attempt) or 0
    )
    return seen


def fake_urlopen(monkeypatch, outcomes):
    """Make urlopen return or raise each outcome in turn, and count the calls."""
    calls = []

    def urlopen(request, timeout):
        outcome = outcomes[len(calls)]
        calls.append(request)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return calls


def test_a_first_success_is_returned_whole_without_waiting(monkeypatch, waits):
    calls = fake_urlopen(monkeypatch, [FakeResponse(b"ok")])

    assert read_url(URL, timeout=30) == Download(URL, b"ok")
    assert len(calls) == 1
    assert waits == []


def test_the_address_returned_is_the_one_the_answer_came_from(monkeypatch, waits):
    # The Senate redirects a missing vote to a "file not found" page, so the
    # votes loader compares this with the address it asked for.
    redirected = FakeResponse(b"<html>Not found</html>")
    redirected.url = "https://example.test/not-found.htm"
    fake_urlopen(monkeypatch, [redirected])

    assert read_url(URL, timeout=30).url == "https://example.test/not-found.htm"


@pytest.mark.parametrize("code", [403, 429, 500, 502, 503, 504])
def test_a_passing_failure_is_tried_again(monkeypatch, waits, code):
    calls = fake_urlopen(monkeypatch, [http_error(code), FakeResponse(b"ok")])

    assert read_url(URL, timeout=30).body == b"ok"
    assert len(calls) == 2
    assert waits == [1]


@pytest.mark.parametrize("code", [400, 401, 404, 410])
def test_a_lasting_failure_is_raised_at_once(monkeypatch, waits, code):
    calls = fake_urlopen(monkeypatch, [http_error(code)])

    with pytest.raises(urllib.error.HTTPError) as raised:
        read_url(URL, timeout=30)
    assert raised.value.code == code
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        ConnectionResetError("connection reset by peer"),
    ],
    ids=["dropped connection", "timeout", "reset before the headers"],
)
def test_a_network_failure_before_the_headers_is_tried_again(monkeypatch, waits, error):
    calls = fake_urlopen(monkeypatch, [error, FakeResponse(b"ok")])

    assert read_url(URL, timeout=30).body == b"ok"
    assert len(calls) == 2
    assert waits == [1]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("The read operation timed out"),
        ConnectionResetError("connection reset by peer"),
        http.client.IncompleteRead(b"half", 100),
    ],
    ids=["stalled", "reset", "cut short"],
)
def test_a_body_that_fails_partway_is_tried_again(monkeypatch, waits, error):
    # The case that started this: the headers arrived, then the body stalled,
    # after open_url had already returned, so nothing tried again.
    stalled = StallingResponse(error)
    calls = fake_urlopen(monkeypatch, [stalled, FakeResponse(b"ok")])

    assert read_url(URL, timeout=30).body == b"ok"
    assert len(calls) == 2
    assert waits == [1]
    assert stalled.closed_by_caller


def test_a_body_that_stalls_every_time_raises_after_the_last_attempt(
    monkeypatch, waits
):
    stalls = [StallingResponse(TimeoutError("timed out")) for _ in range(ATTEMPTS)]
    calls = fake_urlopen(monkeypatch, stalls)

    with pytest.raises(TimeoutError):
        read_url(URL, timeout=30)
    assert len(calls) == ATTEMPTS
    assert waits == [1, 2, 3]


def test_it_gives_up_after_the_last_attempt_with_the_last_error(monkeypatch, waits):
    errors = [http_error(503) for _ in range(ATTEMPTS)]
    calls = fake_urlopen(monkeypatch, errors)

    with pytest.raises(urllib.error.HTTPError) as raised:
        read_url(URL, timeout=30)
    assert raised.value is errors[-1]
    assert len(calls) == ATTEMPTS
    assert waits == [1, 2, 3]


def test_a_failed_response_is_closed_before_trying_again(monkeypatch, waits):
    error = http_error(503)
    fake_urlopen(monkeypatch, [error, FakeResponse(b"ok")])

    read_url(URL, timeout=30)
    assert error.closed_by_caller


def test_the_request_and_timeout_are_passed_through_unchanged(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["request"], seen["timeout"] = request, timeout
        return FakeResponse(b"ok")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    read_url(URL, timeout=12)

    assert seen == {"request": URL, "timeout": 12}


@pytest.mark.parametrize(("attempt", "low", "high"), [(1, 1, 2), (2, 2, 3), (3, 4, 5)])
def test_each_wait_doubles_plus_up_to_a_second_of_jitter(attempt, low, high):
    for _ in range(50):
        assert low <= backoff_delay(attempt) <= high


def test_the_senate_refusing_once_does_not_fail_a_vote_download(monkeypatch, waits):
    # senate.gov answered 403 for a vote it served a minute later.
    xml = b"<roll_call_vote><vote_number>261</vote_number></roll_call_vote>"
    calls = fake_urlopen(monkeypatch, [http_error(403), FakeResponse(xml)])

    root = fetch_xml(URL)

    assert root.findtext("vote_number") == "261"
    assert len(calls) == 2


# A cap on the size of a body.


def test_a_body_at_the_cap_is_returned(monkeypatch, waits):
    fake_urlopen(monkeypatch, [FakeResponse(b"x" * 10)])

    assert read_url(URL, timeout=30, max_bytes=10).body == b"x" * 10


def test_a_body_over_the_cap_is_refused_without_trying_again(monkeypatch, waits):
    calls = fake_urlopen(monkeypatch, [FakeResponse(b"x" * 11)])

    with pytest.raises(ValueError, match=f"{URL} is over 10 bytes"):
        read_url(URL, timeout=30, max_bytes=10)
    assert len(calls) == 1
    assert waits == []


def test_a_capped_download_reads_only_one_byte_past_the_cap(monkeypatch, waits):
    asked = []

    class Huge(FakeResponse):
        def read(self, size=-1):
            asked.append(size)
            return super().read(size)

    fake_urlopen(monkeypatch, [Huge(b"x" * 1000)])

    with pytest.raises(ValueError):
        read_url(URL, timeout=30, max_bytes=10)
    assert asked == [11]


# What a retry leaves in the logs.


@pytest.fixture
def warnings(caplog):
    """Capture what read_url logs at WARNING and above."""
    caplog.set_level(logging.WARNING, logger="ingest.fetch")
    return caplog


def logged(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "ingest.fetch"
    ]


def test_each_retry_logs_the_address_the_failure_the_wait_and_the_attempt(
    monkeypatch, warnings
):
    monkeypatch.setattr("ingest.fetch.backoff_delay", lambda attempt: attempt + 0.4)
    slept = []
    monkeypatch.setattr("ingest.fetch.time.sleep", slept.append)
    fake_urlopen(
        monkeypatch,
        [
            http_error(503),
            StallingResponse(TimeoutError("timed out")),
            FakeResponse(b"ok"),
        ],
    )

    read_url(URL, timeout=30)

    assert logged(warnings) == [
        f"{URL} failed with HTTP 503; trying again in 1.4 s (attempt 1 of 4)",
        f"{URL} failed with TimeoutError; trying again in 2.4 s (attempt 2 of 4)",
    ]
    assert {record.levelno for record in warnings.records} == {logging.WARNING}
    # The wait logged is the wait taken.
    assert slept == [1.4, 2.4]


def test_a_dropped_connection_is_named_in_the_log(monkeypatch, waits, warnings):
    error = urllib.error.URLError("connection refused")
    fake_urlopen(monkeypatch, [error, FakeResponse(b"ok")])

    read_url(URL, timeout=30)

    [message] = logged(warnings)
    assert f"{URL} failed with URLError;" in message


def test_a_body_cut_short_is_named_in_the_log(monkeypatch, waits, warnings):
    cut = StallingResponse(http.client.IncompleteRead(b"half", 100))
    fake_urlopen(monkeypatch, [cut, FakeResponse(b"ok")])

    read_url(URL, timeout=30)

    [message] = logged(warnings)
    assert f"{URL} failed with IncompleteRead;" in message


def test_nothing_is_logged_when_the_first_try_works(monkeypatch, waits, warnings):
    fake_urlopen(monkeypatch, [FakeResponse(b"ok")])

    read_url(URL, timeout=30)

    assert logged(warnings) == []


def test_nothing_is_logged_for_a_lasting_failure(monkeypatch, waits, warnings):
    # A 404 is not tried again, so there is no retry to report; the error
    # itself goes to the loader, which records the run as failed.
    fake_urlopen(monkeypatch, [http_error(404)])

    with pytest.raises(urllib.error.HTTPError):
        read_url(URL, timeout=30)

    assert logged(warnings) == []


def test_giving_up_logs_every_retry_before_the_error(monkeypatch, waits, warnings):
    fake_urlopen(monkeypatch, [http_error(503) for _ in range(ATTEMPTS)])

    with pytest.raises(urllib.error.HTTPError):
        read_url(URL, timeout=30)

    attempts = [message.split("(attempt ")[1] for message in logged(warnings)]
    assert attempts == ["1 of 4)", "2 of 4)", "3 of 4)"]


def test_a_retry_logs_the_address_and_never_the_headers(monkeypatch, waits, warnings):
    # The Congress.gov API key travels in a header, so it must never reach a log.
    request = urllib.request.Request(
        f"{URL}?format=json&limit=250", headers={"X-Api-Key": "fake-key-1234"}
    )
    fake_urlopen(monkeypatch, [http_error(503), FakeResponse(b"ok")])

    read_url(request, timeout=30)

    [message] = logged(warnings)
    assert message.startswith(f"{URL}?format=json&limit=250 failed with HTTP 503")
    assert "fake-key-1234" not in warnings.text
    # urllib stores the header as X-api-key, so compare without case.
    assert "x-api-key" not in warnings.text.lower()


def test_a_loader_sends_its_log_lines_to_standard_output(monkeypatch):
    root = logging.getLogger()
    # Start as a loader does, with no handlers, and put the real ones back after.
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)

    configure_logging()
    configure_logging()

    [handler] = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout
    assert handler.formatter is not None
    assert handler.formatter._fmt == LOG_FORMAT
    assert root.level == logging.INFO


def test_every_loader_that_fetches_sets_up_its_log_lines():
    ingest = Path(__file__).resolve().parent.parent / "ingest"
    loaders = [
        path
        for path in ingest.glob("*.py")
        if "from ingest.fetch import read_url" in path.read_text()
        and "def main(" in path.read_text()
    ]
    missing = [
        path.name
        for path in loaders
        if "def main() -> None:\n    configure_logging()\n" not in path.read_text()
    ]

    assert len(loaders) == 5  # votes, bills, members, officials, photos
    assert missing == []


def test_no_loader_reads_a_body_outside_the_retry():
    # open_url returned before the body was read, which is how a stalled
    # body escaped every retry. It is gone; nothing should bring it back.
    ingest = Path(__file__).resolve().parent.parent / "ingest"
    using = [
        path.name
        for path in ingest.glob("*.py")
        if "open_url" in path.read_text() or "urlopen(" in path.read_text()
    ]

    assert using == ["fetch.py"]
