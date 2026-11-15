"""Tests that an address never reaches a log or a traceback, even when a lookup crashes.

Addresses are never stored or logged, as the privacy policy says. The normal
path is tested in test_pages.py. These tests cover what happens when
something goes wrong partway through a lookup, which is where an address
can leak: into an error's message, into the traceback a server prints for
an unhandled error, or into a request URL that an HTTP error quotes.
"""

import logging
import traceback

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.address import Address
from app.geocoder import GeocoderUnavailable, geocode
from app.main import app, get_conn

CAPITOL = (-89.6548, 39.7984)
ADDRESS = {
    "street": "401 S 2nd St",
    "city": "Springfield",
    "state": "IL",
    "zip_code": "62701",
}

# Each part of the address, as typed and as a URL would encode it.
LEAKS = ["401 S 2nd St", "401+S+2nd+St", "401%20S%202nd%20St", "Springfield", "62701"]


def assert_no_address_in(text: str) -> None:
    """Fail if any part of the address, in any spelling, appears in the text."""
    for part in LEAKS:
        assert part not in text, f"the address leaked: found {part!r}"


@pytest.fixture
def client():
    """A client whose server errors are raised in the test instead of answered with 500.

    The database is never reached, because every test replaces the step
    that would use it.
    """
    app.dependency_overrides[get_conn] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def crash(*args, **kwargs):
    """Stand in for a step that fails unexpectedly, such as the database going away."""
    raise RuntimeError("the database went away")


@pytest.mark.parametrize(
    ("path", "module", "send"),
    [
        ("/lookup", "app.main", "json"),
        ("/tiles/officials", "app.pages", "data"),
    ],
)
def test_a_crash_after_the_census_answers_leaks_no_address(
    client, monkeypatch, caplog, path, module, send
):
    monkeypatch.setattr(f"{module}.geocode", lambda address: CAPITOL)
    monkeypatch.setattr(f"{module}.lookup", crash)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as crashed:
        client.post(path, **{send: ADDRESS})

    # What the server would print for this crash: the whole traceback,
    # including any error it was raised from.
    printed = "".join(traceback.format_exception(crashed.value))
    assert "the database went away" in printed
    assert_no_address_in(printed)
    assert_no_address_in(caplog.text)


def test_a_census_error_that_quotes_its_url_leaks_no_address(monkeypatch):
    # An HTTP error's message names the URL it asked for, and the Census URL
    # holds the address.
    def failing_get(url, params, timeout):
        request = httpx2.Request("GET", url, params=params)
        raise httpx2.ConnectError(f"could not reach {request.url}", request=request)

    monkeypatch.setattr("app.geocoder.httpx2.get", failing_get)

    with pytest.raises(GeocoderUnavailable) as failed:
        geocode(Address(**ADDRESS))

    assert_no_address_in("".join(traceback.format_exception(failed.value)))
