"""add bills.title_search, a bill's title as searchable words, with a GIN index

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE bills
            ADD COLUMN title_search tsvector
            GENERATED ALWAYS AS (to_tsvector('english', title)) STORED
        """
    )
    op.execute("CREATE INDEX bills_title_search_idx ON bills USING GIN (title_search)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX bills_title_search_idx")
    op.execute("ALTER TABLE bills DROP COLUMN title_search")
