"""add bills and bill_summaries tables, and bill columns on votes

One row per bill in bills, and one row per version of its official summary
in bill_summaries. A vote names its bill by type and number.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-11

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE votes
            ADD COLUMN bill_type text
                CONSTRAINT votes_bill_type_check
                CHECK (bill_type IN ('hr', 's', 'hjres', 'sjres',
                                     'hconres', 'sconres', 'hres', 'sres')),
            ADD COLUMN bill_number integer,     -- 2403
            ADD CONSTRAINT votes_bill_both_or_neither
                CHECK ((bill_type IS NULL) = (bill_number IS NULL))
        """
    )
    op.execute(
        """
        CREATE TABLE bills (
            id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            congress             smallint NOT NULL,     -- 119
            bill_type            text NOT NULL
                                 CONSTRAINT bills_bill_type_check
                                 CHECK (bill_type IN ('hr', 's', 'hjres', 'sjres',
                                                      'hconres', 'sconres', 'hres', 'sres')),
            bill_number          integer NOT NULL,      -- 2403
            title                text NOT NULL,
            policy_area          text,                  -- 'Labor and Employment'
            sponsor_bioguide_id  text NOT NULL REFERENCES members (bioguide_id),
            introduced_on        date NOT NULL,
            latest_action_on     date,
            latest_action        text,                  -- 'Presented to President.'
            source_url           text NOT NULL,         -- the bill's page on Congress.gov
            source_updated_at    timestamptz NOT NULL,  -- when Congress.gov last changed it
            updated_at           timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT bills_identity_key
                UNIQUE (congress, bill_type, bill_number)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE bill_summaries (
            bill_id              bigint NOT NULL REFERENCES bills (id) ON DELETE CASCADE,
            version_code         text NOT NULL,         -- '55'
            action_on            date NOT NULL,         -- when the bill reached that stage
            action_desc          text NOT NULL,         -- 'Passed Senate'
            summary_text         text NOT NULL,         -- plain text; what the API serves
            summary_html_source  text NOT NULL,         -- as published; never served
            source_updated_at    timestamptz NOT NULL,  -- when Congress.gov last changed it
            updated_at           timestamptz NOT NULL DEFAULT now(),

            PRIMARY KEY (bill_id, version_code)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE bill_summaries")
    op.execute("DROP TABLE bills")
    op.execute(
        """
        ALTER TABLE votes
            DROP COLUMN bill_type,
            DROP COLUMN bill_number
        """
    )
