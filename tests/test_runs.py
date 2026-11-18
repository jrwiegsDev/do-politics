"""Tests for the run log: every ingestion run is recorded, including one that fails."""

import pytest
from helpers import MEMBERS, last_run

from ingest.members import UPSERT_SQL
from ingest.runs import record_run


def member_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM members").fetchone()[0]


def test_a_run_that_finishes_is_marked_succeeded_with_its_row_count(committing_conn):
    with record_run(committing_conn, "test-job", "test-source") as run:
        committing_conn.execute(UPSERT_SQL, MEMBERS[0])
        run.rows_loaded = 1

    recorded = last_run(committing_conn)
    assert (recorded["job"], recorded["source"]) == ("test-job", "test-source")
    assert (recorded["status"], recorded["rows_loaded"]) == ("succeeded", 1)
    assert recorded["error"] is None
    assert recorded["finished_at"] is not None
    assert member_count(committing_conn) == 1


def test_a_run_that_fails_is_marked_failed_and_its_writes_are_undone(committing_conn):
    with (
        pytest.raises(RuntimeError, match="the source went away"),
        record_run(committing_conn, "test-job", "test-source"),
    ):
        committing_conn.execute(UPSERT_SQL, MEMBERS[0])
        raise RuntimeError("the source went away")

    recorded = last_run(committing_conn)
    assert recorded["status"] == "failed"
    assert recorded["error"] == "RuntimeError: the source went away"
    # Nothing was committed, so nothing was kept.
    assert recorded["rows_loaded"] == 0
    assert recorded["finished_at"] is not None
    assert member_count(committing_conn) == 0


def test_a_failed_run_counts_only_the_rows_it_committed(committing_conn):
    with (
        pytest.raises(RuntimeError),
        record_run(committing_conn, "test-job", "test-source") as run,
    ):
        committing_conn.execute(UPSERT_SQL, MEMBERS[0])
        run.rows_loaded += 1
        run.commit(committing_conn)
        # Counted but never committed, so the failure undoes it.
        committing_conn.execute(UPSERT_SQL, MEMBERS[1])
        run.rows_loaded += 1
        raise RuntimeError("the source went away")

    recorded = last_run(committing_conn)
    assert (recorded["status"], recorded["rows_loaded"]) == ("failed", 1)
    assert member_count(committing_conn) == 1


def test_commit_keeps_the_rows_counted_so_far(committing_conn):
    with record_run(committing_conn, "test-job", "test-source") as run:
        run.rows_loaded = 3
        run.commit(committing_conn)
        assert run.rows_committed == 3
        run.rows_loaded = 5
        assert run.rows_committed == 3


def test_a_failed_run_stays_on_record_after_the_error(committing_conn):
    with (
        pytest.raises(ValueError),
        record_run(committing_conn, "test-job", "test-source"),
    ):
        raise ValueError("bad file")

    # After a rollback, only what was committed is still there.
    committing_conn.rollback()
    assert last_run(committing_conn)["status"] == "failed"
