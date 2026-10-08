"""Find the district and the members of Congress for a point."""

from datetime import date

import psycopg

from app.districts import find_district
from app.officials import find_officials


def lookup(
    conn: psycopg.Connection,
    longitude: float,
    latitude: float,
    on_date: date | None = None,
) -> dict | None:
    """Return the district, senators and representative for a point, or None.

    None means the point is in no congressional district. A vacant House
    seat gives a result whose "representative" is None.
    """
    district = find_district(conn, longitude, latitude, on_date)
    if district is None:
        return None

    state_fips, code, name = district
    officials = find_officials(conn, state_fips, code, on_date)
    senators = [o for o in officials if o["chamber"] == "senate"]
    representatives = [o for o in officials if o["chamber"] == "house"]

    return {
        "district": {"state_fips": state_fips, "code": code, "name": name},
        "senators": senators,
        "representative": representatives[0] if representatives else None,
    }
