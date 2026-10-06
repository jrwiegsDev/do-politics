"""baseline: PostGIS and the districts table

The schema as it stood before Alembic was adopted (the old db/init/001 to 003
files). A database that already has these objects is stamped at this revision
instead of running it.

Revision ID: 0001
Revises:
Create Date: 2026-10-05

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.execute(
        """
        CREATE TABLE districts (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            level           text NOT NULL,     -- 'federal'
            chamber         text NOT NULL,     -- 'house'
            state_fips      char(2) NOT NULL,  -- '17'
            code            text NOT NULL,     -- '13'
            geoid           text NOT NULL,     -- '1713'
            name            text NOT NULL,     -- 'Congressional District 13'
            effective_from  date NOT NULL,
            effective_to    date,              -- NULL means still in effect
            source          text NOT NULL,     -- 'tl_2025_17_cd119'
            boundary        geometry(MultiPolygon, 4269) NOT NULL,

            -- One row per district per map. Lets the loader upsert instead of duplicating.
            CONSTRAINT districts_identity_key
                UNIQUE (level, chamber, geoid, effective_from)
        )
        """
    )

    op.execute("CREATE INDEX districts_boundary_gix ON districts USING GIST (boundary)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE districts")
