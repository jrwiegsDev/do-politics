"""add member_photos, one photo per member of Congress

The image itself is stored, with the SHA-256 of its bytes, where it came
from, and whom to credit for it when the source asks for that.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE member_photos (
            bioguide_id  text PRIMARY KEY
                         REFERENCES members (bioguide_id) ON DELETE CASCADE,
            image        bytea NOT NULL
                         CONSTRAINT member_photos_image_size_check
                         CHECK (octet_length(image) BETWEEN 1 AND 1000000),
            sha256       text NOT NULL          -- hex digest of image
                         CONSTRAINT member_photos_sha256_check
                         CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            source       text NOT NULL
                         CONSTRAINT member_photos_source_check
                         CHECK (source IN ('unitedstates', 'congress.gov')),
            source_url   text NOT NULL,         -- where the image was downloaded
            attribution  text,                  -- 'Courtesy U.S. Senate Historical Office'
            updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE member_photos")
