"""Shared test setup: a throwaway database with the schema and Illinois loaded."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from ingest.congressional_districts import effective_from_for, load_state
from ingest.db import connect

TEST_DB = "dopolitics_test"
ILLINOIS = "17"
VINTAGE = 2025
CONGRESS = 119
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def recreate_test_db(create: bool) -> None:
    """Drop the test database, and create it again empty if asked to."""
    # CREATE and DROP DATABASE cannot run inside a transaction, so this
    # connection commits each statement as it goes.
    with connect() as admin:
        admin.autocommit = True
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        if create:
            admin.execute(f"CREATE DATABASE {TEST_DB}")


@pytest.fixture(scope="session")
def database():
    """Build the test database once per test run and drop it afterwards."""
    with pytest.MonkeyPatch.context() as env:
        # Tests only ever use the local POSTGRES_* settings. Ignoring
        # DATABASE_URL means a production URL in the shell can never be touched.
        env.delenv("DATABASE_URL", raising=False)
        recreate_test_db(create=True)

        # From here until the tests finish, connect() opens the test database.
        with pytest.MonkeyPatch.context() as test_env:
            test_env.setenv("POSTGRES_DB", TEST_DB)
            command.upgrade(Config(ALEMBIC_INI), "head")
            with connect() as conn:
                name = f"tl_{VINTAGE}_{ILLINOIS}_cd{CONGRESS}"
                load_state(conn, name, VINTAGE, CONGRESS, effective_from_for(ILLINOIS))

            yield

        recreate_test_db(create=False)


@pytest.fixture
def conn(database):
    """Give each test its own connection to the test database.

    Whatever a test writes is rolled back afterwards, so every test starts
    from the same data: the schema plus Illinois's districts.
    """
    with connect() as conn:
        yield conn
        conn.rollback()
