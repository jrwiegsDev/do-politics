"""Tests for the district lookup."""

from datetime import date

from app.districts import find_district
from ingest.congressional_districts import CLOSE_OLDER_MAPS_SQL

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


def maps_containing_the_capitol(conn, on_date):
    """Return the source file of every row that holds the Capitol on a date.

    find_district returns only one row, so a second map still in effect would
    go unnoticed there. This sees all of them.
    """
    rows = conn.execute(
        """
        SELECT source
        FROM districts
        WHERE ST_Contains(boundary, ST_Point(%(longitude)s, %(latitude)s, 4269))
          AND effective_from <= %(on_date)s
          AND (effective_to IS NULL OR effective_to > %(on_date)s)
        ORDER BY source;
        """,
        {
            "longitude": CAPITOL_LONGITUDE,
            "latitude": CAPITOL_LATITUDE,
            "on_date": on_date,
        },
    ).fetchall()
    return [source for (source,) in rows]


def test_the_119th_map_holds_until_the_120th_congress_is_seated(conn):
    assert maps_containing_the_capitol(conn, date(2027, 1, 2)) == ["tl_2025_17_cd119"]


def test_the_120th_map_starts_the_day_the_120th_congress_is_seated(conn):
    assert maps_containing_the_capitol(conn, date(2027, 1, 3)) == ["tl_2026_17_cd120"]


def test_the_capitol_is_still_in_district_13_under_the_120th_map(conn):
    district = find_district(
        conn, CAPITOL_LONGITUDE, CAPITOL_LATITUDE, on_date=date(2027, 6, 1)
    )

    assert district == ("17", "13", "Congressional District 13")


def test_closing_older_maps_again_changes_nothing(conn):
    with conn.cursor() as cur:
        cur.execute(
            CLOSE_OLDER_MAPS_SQL,
            {"state_fips": "17", "effective_from": date(2027, 1, 3)},
        )

        assert cur.rowcount == 0


def test_a_later_map_closes_only_the_map_still_in_effect(conn):
    with conn.cursor() as cur:
        cur.execute(
            CLOSE_OLDER_MAPS_SQL,
            {"state_fips": "17", "effective_from": date(2029, 1, 3)},
        )

        assert cur.rowcount == 17

    ends = conn.execute(
        "SELECT source, effective_to FROM districts GROUP BY source, effective_to"
    ).fetchall()
    assert sorted(ends) == [
        ("tl_2025_17_cd119", date(2027, 1, 3)),
        ("tl_2026_17_cd120", date(2029, 1, 3)),
    ]
