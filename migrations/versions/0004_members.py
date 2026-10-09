"""add members table

One row per person who has served in the 119th Congress, sitting or
departed. Votes are cast by people, and a member's votes outlast their
seat, so this table never loses a row when someone leaves office.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE members (
            bioguide_id  text PRIMARY KEY,   -- 'B001315'
            lis_id       text UNIQUE,        -- 'S293'; NULL unless they served in the Senate
            name         text NOT NULL,      -- 'Nikki Budzinski'
            source       text NOT NULL,      -- 'legislators-current' or 'legislators-historical'
            updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE members")
