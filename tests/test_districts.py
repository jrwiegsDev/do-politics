"""Tests for the district lookup."""

from datetime import date

from app.districts import find_district

CAPITOL_LONGITUDE = -89.6548
CAPITOL_LATITUDE = 39.7984


def test_illinois_state_capitol_is_in_district_13(conn):
    district = find_district(conn, CAPITOL_LONGITUDE, CAPITOL_LATITUDE)

    assert district == ("17", "13", "Congressional District 13")


def test_point_in_the_ocean_has_no_district(conn):
    district = find_district(conn, -40.0, 35.0)

    assert district is None


def test_lookup_before_the_map_took_effect_finds_nothing(conn):
    district = find_district(
        conn, CAPITOL_LONGITUDE, CAPITOL_LATITUDE, on_date=date(2022, 6, 1)
    )

    assert district is None


def test_scott_air_force_base_is_in_neighboring_district_12(conn):
    district = find_district(conn, -89.85, 38.545)

    assert district == ("17", "12", "Congressional District 12")
