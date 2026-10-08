"""add officials table

One row per sitting member of Congress, holding their current seat and
DC office contact details. A vacant seat has no row.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE officials (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            bioguide_id     text NOT NULL UNIQUE,  -- 'B001315'
            name            text NOT NULL,         -- 'Nikki Budzinski'
            party           text NOT NULL,         -- 'Democrat'
            chamber         text NOT NULL
                            CHECK (chamber IN ('senate', 'house')),
            state           char(2) NOT NULL,      -- 'IL'
            state_fips      char(2) NOT NULL,      -- '17', matches districts.state_fips
            district_code   text,                  -- '13', matches districts.code; NULL for senators
            term_start      date NOT NULL,
            term_end        date NOT NULL,
            phone           text,
            office_address  text,
            website         text,
            contact_form    text,
            source          text NOT NULL,         -- 'legislators-current'
            updated_at      timestamptz NOT NULL DEFAULT now(),

            -- A House member has a district; a senator does not.
            CONSTRAINT officials_district_matches_chamber
                CHECK ((chamber = 'house') = (district_code IS NOT NULL))
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE officials")
