"""Find the members of Congress who represent a state and district."""

from datetime import date

import psycopg
from psycopg.rows import dict_row

FIND_OFFICIALS_SQL = """
SELECT bioguide_id, name, party, chamber, state, district_code,
       phone, office_address, website, contact_form
FROM officials
WHERE state_fips = %(state_fips)s
  AND (chamber = 'senate' OR district_code = %(district_code)s)
  AND term_start <= %(on_date)s
  AND term_end > %(on_date)s
ORDER BY chamber DESC, name;
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
