"""Tests for the Census Geocoder client. No test here calls the real service."""

import logging

import httpx2
import pytest

from app.geocoder import GEOCODER_URL, GeocoderUnavailable, geocode

ADDRESS = "401 S 2nd St, Springfield, IL 62701"
MATCH = {"result": {"addressMatches": [{"coordinates": {"x": -89.6535, "y": 39.7983}}]}}
NO_MATCH = {"result": {"addressMatches": []}}


def answer_with(monkeypatch, status_code, body):
    """Make httpx2.get return a canned response instead of calling the Census."""
    request = httpx2.Request("GET", GEOCODER_URL, params={"address": ADDRESS})
    response = httpx2.Response(status_code, json=body, request=request)
    monkeypatch.setattr("app.geocoder.httpx2.get", lambda *args, **kwargs: response)


def test_matched_address_returns_longitude_then_latitude(monkeypatch):
    answer_with(monkeypatch, 200, MATCH)

    assert geocode(ADDRESS) == (-89.6535, 39.7983)


def test_address_with_no_match_returns_none(monkeypatch):
    answer_with(monkeypatch, 200, NO_MATCH)

    assert geocode(ADDRESS) is None


def test_server_error_means_the_geocoder_is_unavailable(monkeypatch):
    answer_with(monkeypatch, 500, {})

    with pytest.raises(GeocoderUnavailable):
        geocode(ADDRESS)


def test_unexpected_response_shape_means_the_geocoder_is_unavailable(monkeypatch):
    answer_with(monkeypatch, 200, {"unexpected": "shape"})

    with pytest.raises(GeocoderUnavailable):
        geocode(ADDRESS)


def test_timeout_means_the_geocoder_is_unavailable(monkeypatch):
    def time_out(*args, **kwargs):
        raise httpx2.ConnectTimeout("timed out")

    monkeypatch.setattr("app.geocoder.httpx2.get", time_out)

    with pytest.raises(GeocoderUnavailable):
        geocode(ADDRESS)


def test_the_error_never_carries_the_address(monkeypatch):
    answer_with(monkeypatch, 500, {})

    with pytest.raises(GeocoderUnavailable) as raised:
        geocode(ADDRESS)

    assert "Springfield" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_the_address_is_not_written_to_the_log(monkeypatch, caplog):
    # Replace only the network layer, so the HTTP client's own logging still runs.
    monkeypatch.setattr(
        httpx2.HTTPTransport,
        "handle_request",
        lambda self, request: httpx2.Response(200, json=MATCH),
    )
    caplog.set_level(logging.INFO)

    assert geocode(ADDRESS) == (-89.6535, 39.7983)
    assert "Springfield" not in caplog.text
