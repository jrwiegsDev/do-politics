"""add bill_titles, every distinct title a bill has gone by, as searchable words

Congress.gov lists a bill's titles: its official title, its short titles
(as introduced, as passed, as enacted), short titles for portions of the
bill, and a popular title for the best known. One row per distinct title,
with the kind of title it is, and a GIN index on its words.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-22

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE bill_titles (
            bill_id       bigint NOT NULL REFERENCES bills (id) ON DELETE CASCADE,
            title         text NOT NULL,
            kind          text NOT NULL,  -- 'popular', 'short', 'portion', ...
            title_search  tsvector
                          GENERATED ALWAYS AS (to_tsvector('english', title)) STORED,

            PRIMARY KEY (bill_id, title),
            CONSTRAINT bill_titles_kind_check CHECK (
                kind IN ('popular', 'short', 'portion', 'official', 'display')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX bill_titles_search_idx ON bill_titles USING GIN (title_search)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE bill_titles")
