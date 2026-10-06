"""Alembic environment: tells Alembic how to reach the database.

Alembic runs this file every time an `alembic` command is used.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from ingest.db import connect

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations here are written as plain SQL, so there are no SQLAlchemy models
# for Alembic to compare the database against.
target_metadata = None


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect the same way the ingestion jobs do, then run the migrations."""
    engine = create_engine(
        "postgresql+psycopg://",
        creator=connect,
        poolclass=pool.NullPool,
    )

    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
