"""Turn a street address into a point with the U.S. Census Geocoder."""

import logging

import httpx2

GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
BENCHMARK = "Public_AR_Current"
TIMEOUT_SECONDS = 10

# httpx2 logs every request URL at INFO level, and this URL contains the
# address. Addresses are never logged, so only warnings and errors get through.
logging.getLogger("httpx2").setLevel(logging.WARNING)


class GeocoderUnavailable(Exception):
    """The Census Geocoder could not be reached or gave an unusable answer."""


def geocode(address: str) -> tuple[float, float] | None:
    """Return the (longitude, latitude) of a street address, or None if it has no match.

    The coordinates are NAD83, the same system the district boundaries use.
    """
    params = {"address": address, "benchmark": BENCHMARK, "format": "json"}
    try:
        response = httpx2.get(GEOCODER_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        matches = response.json()["result"]["addressMatches"]
        if not matches:
            return None
        coordinates = matches[0]["coordinates"]
        return (coordinates["x"], coordinates["y"])
    except (httpx2.HTTPError, KeyError, TypeError, ValueError) as error:
        # Only the kind of error is kept. Its message and its traceback hold
        # the request URL, and the URL holds the address.
        raise GeocoderUnavailable(type(error).__name__) from None
