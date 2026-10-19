"""add generated_summaries, plain-language text generated from a bill's source

One row per bill, source version, prompt version and model, plus revisions:
a row that rewrites an approved row to address its review note points back
to it, and each row can be revised once. A row starts as a draft and is
shown on the site only after it is approved.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE generated_summaries (
            id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            bill_id              bigint NOT NULL REFERENCES bills (id) ON DELETE CASCADE,
            source               text NOT NULL
                                 CONSTRAINT generated_summaries_source_check
                                 CHECK (source IN ('crs_summary', 'bill_text')),
            source_version_code  text NOT NULL,         -- '55': the version it was written from
            plain_summary        text NOT NULL,
            impact               text NOT NULL,         -- the "how this does you" callout
            model                text NOT NULL,         -- 'claude-opus-5-5'
            prompt_version       text NOT NULL,         -- '1'
            status               text NOT NULL DEFAULT 'draft'
                                 CONSTRAINT generated_summaries_status_check
                                 CHECK (status IN ('draft', 'approved', 'rejected')),
            review_note          text,
            reviewed_at          timestamptz,
            input_tokens         integer NOT NULL,
            output_tokens        integer NOT NULL,      -- includes thinking
            cost_usd             numeric(10, 6) NOT NULL,
            generated_at         timestamptz NOT NULL DEFAULT now(),
            revised_from         bigint REFERENCES generated_summaries (id)
                                 ON DELETE CASCADE,  -- the approved row this rewrites

            CONSTRAINT generated_summaries_reviewed_check
                CHECK ((status = 'draft') = (reviewed_at IS NULL))
        )
        """
    )
    # Two revisions of one bill, such as a revision of a revision, share the
    # bill, version, revision prompt and model, so the one-answer-each rule
    # covers originals only. Each row can be revised once.
    op.execute(
        """
        CREATE UNIQUE INDEX generated_summaries_identity_key
            ON generated_summaries (bill_id, source, source_version_code, prompt_version, model)
            WHERE revised_from IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX generated_summaries_revised_from_key
            ON generated_summaries (revised_from)
            WHERE revised_from IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE generated_summaries")
