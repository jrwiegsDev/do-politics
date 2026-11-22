"""add states and the daily usage counts: lookups, bill views and searches

Each count table holds one row per day per thing counted, and a visit adds 1
to its row. No column can hold anything about a visitor: no address, IP
address, search words or time of day. states is the table the lookup counts
join to for a state's name, Census region and Census division.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Everything with a seat in Congress, with the Census Bureau's regions and
# divisions. The Census puts the five territories in no region; "Territories"
# is this project's label for them, so every state has a region to roll up to.
STATES = [
    ("01", "AL", "Alabama", "South", "East South Central"),
    ("02", "AK", "Alaska", "West", "Pacific"),
    ("04", "AZ", "Arizona", "West", "Mountain"),
    ("05", "AR", "Arkansas", "South", "West South Central"),
    ("06", "CA", "California", "West", "Pacific"),
    ("08", "CO", "Colorado", "West", "Mountain"),
    ("09", "CT", "Connecticut", "Northeast", "New England"),
    ("10", "DE", "Delaware", "South", "South Atlantic"),
    ("11", "DC", "District of Columbia", "South", "South Atlantic"),
    ("12", "FL", "Florida", "South", "South Atlantic"),
    ("13", "GA", "Georgia", "South", "South Atlantic"),
    ("15", "HI", "Hawaii", "West", "Pacific"),
    ("16", "ID", "Idaho", "West", "Mountain"),
    ("17", "IL", "Illinois", "Midwest", "East North Central"),
    ("18", "IN", "Indiana", "Midwest", "East North Central"),
    ("19", "IA", "Iowa", "Midwest", "West North Central"),
    ("20", "KS", "Kansas", "Midwest", "West North Central"),
    ("21", "KY", "Kentucky", "South", "East South Central"),
    ("22", "LA", "Louisiana", "South", "West South Central"),
    ("23", "ME", "Maine", "Northeast", "New England"),
    ("24", "MD", "Maryland", "South", "South Atlantic"),
    ("25", "MA", "Massachusetts", "Northeast", "New England"),
    ("26", "MI", "Michigan", "Midwest", "East North Central"),
    ("27", "MN", "Minnesota", "Midwest", "West North Central"),
    ("28", "MS", "Mississippi", "South", "East South Central"),
    ("29", "MO", "Missouri", "Midwest", "West North Central"),
    ("30", "MT", "Montana", "West", "Mountain"),
    ("31", "NE", "Nebraska", "Midwest", "West North Central"),
    ("32", "NV", "Nevada", "West", "Mountain"),
    ("33", "NH", "New Hampshire", "Northeast", "New England"),
    ("34", "NJ", "New Jersey", "Northeast", "Middle Atlantic"),
    ("35", "NM", "New Mexico", "West", "Mountain"),
    ("36", "NY", "New York", "Northeast", "Middle Atlantic"),
    ("37", "NC", "North Carolina", "South", "South Atlantic"),
    ("38", "ND", "North Dakota", "Midwest", "West North Central"),
    ("39", "OH", "Ohio", "Midwest", "East North Central"),
    ("40", "OK", "Oklahoma", "South", "West South Central"),
    ("41", "OR", "Oregon", "West", "Pacific"),
    ("42", "PA", "Pennsylvania", "Northeast", "Middle Atlantic"),
    ("44", "RI", "Rhode Island", "Northeast", "New England"),
    ("45", "SC", "South Carolina", "South", "South Atlantic"),
    ("46", "SD", "South Dakota", "Midwest", "West North Central"),
    ("47", "TN", "Tennessee", "South", "East South Central"),
    ("48", "TX", "Texas", "South", "West South Central"),
    ("49", "UT", "Utah", "West", "Mountain"),
    ("50", "VT", "Vermont", "Northeast", "New England"),
    ("51", "VA", "Virginia", "South", "South Atlantic"),
    ("53", "WA", "Washington", "West", "Pacific"),
    ("54", "WV", "West Virginia", "South", "South Atlantic"),
    ("55", "WI", "Wisconsin", "Midwest", "East North Central"),
    ("56", "WY", "Wyoming", "West", "Mountain"),
    ("60", "AS", "American Samoa", "Territories", "Territories"),
    ("66", "GU", "Guam", "Territories", "Territories"),
    ("69", "MP", "Northern Mariana Islands", "Territories", "Territories"),
    ("72", "PR", "Puerto Rico", "Territories", "Territories"),
    ("78", "VI", "U.S. Virgin Islands", "Territories", "Territories"),
]

# A description of the states table for op.bulk_insert below: just enough
# for SQLAlchemy to write the INSERT, not a model the app uses.
states_table = sa.table(
    "states",
    sa.column("fips"),
    sa.column("postal"),
    sa.column("name"),
    sa.column("region"),
    sa.column("division"),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE states (
            fips      text PRIMARY KEY,      -- '17', how our other tables name a state
            postal    text NOT NULL UNIQUE,  -- 'IL'
            name      text NOT NULL,         -- 'Illinois'
            region    text NOT NULL,         -- 'Midwest'
            division  text NOT NULL          -- 'East North Central'
        )
        """
    )
    op.bulk_insert(
        states_table,
        [
            {"fips": f, "postal": p, "name": n, "region": r, "division": d}
            for f, p, n, r, d in STATES
        ],
    )
    op.execute(
        """
        CREATE TABLE lookup_counts (
            day         date NOT NULL,                  -- the day in Central time
            outcome     text NOT NULL,                  -- 'found', 'not_found', ...
            state_fips  text REFERENCES states (fips),  -- only when found
            lookups     integer NOT NULL,

            CONSTRAINT lookup_counts_key
                UNIQUE NULLS NOT DISTINCT (day, outcome, state_fips),
            CONSTRAINT lookup_counts_outcome_check CHECK (
                outcome IN ('found', 'not_found', 'no_district',
                            'unavailable', 'rate_limited', 'invalid')
            ),
            CONSTRAINT lookup_counts_state_check
                CHECK ((outcome = 'found') = (state_fips IS NOT NULL)),
            CONSTRAINT lookup_counts_lookups_check CHECK (lookups > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE bill_view_counts (
            day          date NOT NULL,
            bill_id      bigint NOT NULL REFERENCES bills (id) ON DELETE CASCADE,
            opened_from  text NOT NULL,  -- 'search' or 'votes'
            views        integer NOT NULL,

            PRIMARY KEY (day, bill_id, opened_from),
            CONSTRAINT bill_view_counts_opened_from_check
                CHECK (opened_from IN ('search', 'votes')),
            CONSTRAINT bill_view_counts_views_check CHECK (views > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE search_counts (
            day             date PRIMARY KEY,
            requests        integer NOT NULL,  -- first pages of results asked for
            empty_requests  integer NOT NULL,  -- of those, how many found nothing

            CONSTRAINT search_counts_requests_check CHECK (requests > 0),
            CONSTRAINT search_counts_empty_check
                CHECK (empty_requests BETWEEN 0 AND requests)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE search_counts")
    op.execute("DROP TABLE bill_view_counts")
    op.execute("DROP TABLE lookup_counts")
    op.execute("DROP TABLE states")
