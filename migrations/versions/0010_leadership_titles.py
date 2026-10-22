"""add officials.leadership_titles, the party and chamber leadership roles a member holds

An empty list for most members. The roles a member holds are what decide
whether they are paid more than the base salary.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE officials
            ADD COLUMN leadership_titles text[] NOT NULL DEFAULT '{}'  -- 'Speaker of the House'
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE officials DROP COLUMN leadership_titles")
