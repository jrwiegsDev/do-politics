"""add bill_actions, every step a bill has taken, oldest first

An action has no identifier of its own, and two can be identical (one
referral row per committee), so a row is keyed by its bill and its position
in the list.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE bill_actions (
            bill_id      bigint NOT NULL REFERENCES bills (id) ON DELETE CASCADE,
            position     smallint NOT NULL,     -- 0 is the oldest step
            acted_on     date NOT NULL,
            acted_at     time,                  -- floor actions only; Eastern time
            action_code  text,                  -- '8000'; some actions have none
            action_type  text NOT NULL,         -- 'Floor', 'IntroReferral', 'BecameLaw'
            source       text NOT NULL,         -- 'Library of Congress', 'Senate'
            action_text  text NOT NULL,         -- 'Passed/agreed to in House: ...'

            PRIMARY KEY (bill_id, position),
            CONSTRAINT bill_actions_position_check CHECK (position >= 0)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE bill_actions")
