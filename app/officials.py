"""Find the members of Congress who represent a state and district."""

from datetime import date

import psycopg
from psycopg.rows import dict_row

FIND_OFFICIALS_SQL = """
SELECT bioguide_id, name, party, chamber, state, district_code,
       phone, office_address, website, contact_form,
       p.bioguide_id IS NOT NULL AS has_photo, p.attribution AS photo_credit
FROM officials
LEFT JOIN member_photos AS p USING (bioguide_id)
WHERE state_fips = %(state_fips)s
  AND (chamber = 'senate' OR district_code = %(district_code)s)
  AND term_start <= %(on_date)s
  AND term_end > %(on_date)s
ORDER BY chamber DESC, name;
"""

FIND_OFFICIAL_SQL = """
SELECT bioguide_id, name, party, chamber, state, district_code,
       phone, office_address, website, contact_form,
       term_start, leadership_titles,
       p.bioguide_id IS NOT NULL AS has_photo, p.attribution AS photo_credit
FROM officials
LEFT JOIN member_photos AS p USING (bioguide_id)
WHERE bioguide_id = %(bioguide_id)s;
"""


def find_officials(
    conn: psycopg.Connection,
    state_fips: str,
    district_code: str,
    on_date: date | None = None,
) -> list[dict]:
    """Return the sitting senators, then the representative, for a state and district.

    A vacant House seat has no row, so the list then holds only the senators.
    """
    params = {
        "state_fips": state_fips,
        "district_code": district_code,
        "on_date": on_date or date.today(),
    }
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(FIND_OFFICIALS_SQL, params).fetchall()


def find_official(conn: psycopg.Connection, bioguide_id: str) -> dict | None:
    """Return the sitting member with this Bioguide ID, or None if nobody holds a seat."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(FIND_OFFICIAL_SQL, {"bioguide_id": bioguide_id}).fetchone()
