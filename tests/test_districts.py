"""Tests for the district lookup."""

from datetime import date

from app.districts import find_district

ARCH_LONGITUDE = -90.1848
ARCH_LATITUDE = 38.6247


def test_gateway_arch_is_in_missouri_district_1(conn):
    district = find_district(conn, ARCH_LONGITUDE, ARCH_LATITUDE)

    assert district == ("29", "01", "Congressional District 1")


def test_point_in_the_ocean_has_no_district(conn):
    district = find_district(conn, -40.0, 35.0)

    assert district is None


def test_lookup_before_the_map_took_effect_finds_nothing(conn):
    district = find_district(conn, ARCH_LONGITUDE, ARCH_LATITUDE, on_date=date(2022, 6, 1))

    assert district is None
