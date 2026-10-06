"""add ingestion_runs table

One row per run of an ingestion job, so we can see what ran, when, and
whether it worked.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE ingestion_runs (
            id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            job          text NOT NULL,         -- 'congressional_districts'
            source       text NOT NULL,         -- 'tl_2025_17_cd119'
            status       text NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at   timestamptz NOT NULL DEFAULT now(),
            finished_at  timestamptz,           -- NULL while running
            rows_loaded  integer,
            error        text
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE ingestion_runs")
