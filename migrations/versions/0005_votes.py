"""add votes and vote_positions tables

One row per roll call in votes, and one row per member per roll call in
vote_positions. A position belongs to one vote and one member.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE votes (
            id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            chamber      text NOT NULL
                         CHECK (chamber IN ('senate', 'house')),
            congress     smallint NOT NULL,     -- 119
            session      smallint NOT NULL,     -- 2
            roll_number  integer NOT NULL,      -- 100
            voted_at     timestamptz NOT NULL,
            question     text,                  -- 'On Motion to Recommit'
            vote_type    text,                  -- 'YEA-AND-NAY', 'QUORUM'
            issue        text,                  -- 'H R 5103', 'PN12-1'
            title        text,                  -- the bill's short title or the vote's description
            result       text NOT NULL,         -- 'Failed', 'Nomination Confirmed'
            source_url   text NOT NULL,
            updated_at   timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT votes_identity_key
                UNIQUE (chamber, congress, session, roll_number)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE vote_positions (
            vote_id      bigint NOT NULL REFERENCES votes (id) ON DELETE CASCADE,
            bioguide_id  text NOT NULL REFERENCES members (bioguide_id),
            position     text NOT NULL,         -- 'Yea', 'Not Voting'; free text
            party        text NOT NULL,         -- 'D', as recorded that day
            state        char(2) NOT NULL,      -- 'IL', as recorded that day

            PRIMARY KEY (vote_id, bioguide_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX vote_positions_bioguide_id_idx ON vote_positions (bioguide_id)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE vote_positions")
    op.execute("DROP TABLE votes")
