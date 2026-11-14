"""Tests for open_url: which failures are tried again, how many times, and how long it waits."""

import io
import urllib.error
from email.message import Message

import pytest

from ingest.fetch import ATTEMPTS, backoff_delay, open_url
from ingest.votes import fetch_xml

URL = "https://example.test/vote.xml"


class FakeResponse(io.BytesIO):
    """Stands in for what urlopen returns: a file to read, at the address asked for."""

    url = URL


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
    """Record each attempt number open_url waits after, instead of waiting."""
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


def test_a_first_success_is_returned_without_waiting(monkeypatch, waits):
    calls = fake_urlopen(monkeypatch, [FakeResponse(b"ok")])

    with open_url(URL, timeout=30) as response:
        assert response.read() == b"ok"
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize("code", [403, 429, 500, 502, 503, 504])
def test_a_passing_failure_is_tried_again(monkeypatch, waits, code):
    calls = fake_urlopen(monkeypatch, [http_error(code), FakeResponse(b"ok")])

    with open_url(URL, timeout=30) as response:
        assert response.read() == b"ok"
    assert len(calls) == 2
    assert waits == [1]


@pytest.mark.parametrize("code", [400, 401, 404, 410])
def test_a_lasting_failure_is_raised_at_once(monkeypatch, waits, code):
    calls = fake_urlopen(monkeypatch, [http_error(code)])

    with pytest.raises(urllib.error.HTTPError) as raised:
        open_url(URL, timeout=30)
    assert raised.value.code == code
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("connection refused"), TimeoutError("timed out")],
    ids=["dropped connection", "timeout"],
)
def test_a_network_failure_is_tried_again(monkeypatch, waits, error):
    calls = fake_urlopen(monkeypatch, [error, FakeResponse(b"ok")])

    with open_url(URL, timeout=30) as response:
        assert response.read() == b"ok"
    assert len(calls) == 2
    assert waits == [1]


def test_it_gives_up_after_the_last_attempt_with_the_last_error(monkeypatch, waits):
    errors = [http_error(503) for _ in range(ATTEMPTS)]
    calls = fake_urlopen(monkeypatch, errors)

    with pytest.raises(urllib.error.HTTPError) as raised:
        open_url(URL, timeout=30)
    assert raised.value is errors[-1]
    assert len(calls) == ATTEMPTS
    assert waits == [1, 2, 3]


def test_a_failed_response_is_closed_before_trying_again(monkeypatch):
    error = http_error(503)
    fake_urlopen(monkeypatch, [error, FakeResponse(b"ok")])

    open_url(URL, timeout=30).close()
    assert error.closed_by_caller


def test_the_request_and_timeout_are_passed_through_unchanged(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["request"], seen["timeout"] = request, timeout
        return FakeResponse(b"ok")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    open_url(URL, timeout=12).close()

    assert seen == {"request": URL, "timeout": 12}


@pytest.mark.parametrize(("attempt", "low", "high"), [(1, 1, 2), (2, 2, 3), (3, 4, 5)])
def test_each_wait_doubles_plus_up_to_a_second_of_jitter(attempt, low, high):
    for _ in range(50):
        assert low <= backoff_delay(attempt) <= high


def test_the_senate_refusing_once_does_not_fail_a_vote_download(monkeypatch):
    # The case that started this: senate.gov answered 403 for a vote it
    # served a minute later.
    xml = b"<roll_call_vote><vote_number>261</vote_number></roll_call_vote>"
    calls = fake_urlopen(monkeypatch, [http_error(403), FakeResponse(xml)])

    root = fetch_xml(URL)

    assert root.findtext("vote_number") == "261"
    assert len(calls) == 2
