"""Find the congressional district that contains a point."""

from datetime import date

import psycopg

FIND_DISTRICT_SQL = """
SELECT state_fips, code, name
FROM districts
WHERE ST_Contains(boundary, ST_Point(%(longitude)s, %(latitude)s, 4269))
  AND effective_from <= %(on_date)s
  AND (effective_to IS NULL OR effective_to > %(on_date)s);
"""


def find_district(
    conn: psycopg.Connection,
    longitude: float,
    latitude: float,
    on_date: date | None = None,
) -> tuple[str, str, str] | None:
    """Return the (state_fips, code, name) of the district containing the point, or None."""
    params = {
        "longitude": longitude,
        "latitude": latitude,
        "on_date": on_date or date.today(),
    }
    return conn.execute(FIND_DISTRICT_SQL, params).fetchone()
