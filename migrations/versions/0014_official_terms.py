"""add official_terms, every term a sitting member has served, oldest first

A term is a row as congress-legislators lists it: one per House term (two
years) and one per Senate term (six years), with a separate row for a
partial term reached by appointment or special election. Rows are keyed
by the member and their position in that list.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-22

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE official_terms (
            bioguide_id  text NOT NULL
                         REFERENCES officials (bioguide_id) ON DELETE CASCADE,
            position     smallint NOT NULL,  -- 0 is the first term served
            chamber      text NOT NULL,      -- 'house' or 'senate'
            state        text NOT NULL,      -- 'IL'
            district     smallint,           -- House only; 0 means at-large
            party        text NOT NULL,      -- 'Democrat'
            starts_on    date NOT NULL,
            ends_on      date NOT NULL,      -- in the future for the current term
            how          text,               -- 'appointment', 'special-election'

            PRIMARY KEY (bioguide_id, position),
            CONSTRAINT official_terms_position_check CHECK (position >= 0),
            CONSTRAINT official_terms_chamber_check
                CHECK (chamber IN ('house', 'senate')),
            CONSTRAINT official_terms_district_check
                CHECK ((chamber = 'house') = (district IS NOT NULL)),
            CONSTRAINT official_terms_dates_check CHECK (ends_on > starts_on)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE official_terms")
