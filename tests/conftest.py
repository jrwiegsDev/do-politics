"""Shared test setup: a throwaway database with the schema and Illinois loaded."""

from datetime import date
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
# Illinois's map for the 120th Congress is loaded too, so the tests can check
# the switch from one map to the next on the day the new Congress is seated.
NEXT_VINTAGE = 2026
NEXT_CONGRESS = 120
NEXT_CONGRESS_SEATED = date(2027, 1, 3)
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
                next_name = f"tl_{NEXT_VINTAGE}_{ILLINOIS}_cd{NEXT_CONGRESS}"
                load_state(
                    conn, next_name, NEXT_VINTAGE, NEXT_CONGRESS, NEXT_CONGRESS_SEATED
                )

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


@pytest.fixture
def committing_conn(conn):
    """A connection for testing code that commits, such as a loader's load().

    The usual rollback cannot undo a commit, so this removes what the test
    left behind, and the next test still starts from the same data. Votes and
    bills go first, because a member with a recorded vote or a sponsored bill
    cannot be deleted.
    """
    last_run = conn.execute("SELECT coalesce(max(id), 0) FROM ingestion_runs")
    last_run_id = last_run.fetchone()[0]
    conn.commit()

    yield conn

    conn.rollback()
    conn.execute("DELETE FROM votes")
    conn.execute("DELETE FROM bills")
    conn.execute("DELETE FROM officials")
    conn.execute("DELETE FROM members")
    conn.execute("DELETE FROM ingestion_runs WHERE id > %s", [last_run_id])
    conn.commit()
