"""add a category column to votes

Each vote is one of three kinds: legislation, nomination or procedural.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE votes
            ADD COLUMN category text NOT NULL DEFAULT 'procedural'
            CONSTRAINT votes_category_check
                CHECK (category IN ('legislation', 'nomination', 'procedural'))
        """
    )
    op.execute(
        """
        ALTER TABLE votes ALTER COLUMN category DROP DEFAULT
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE votes DROP COLUMN category")
