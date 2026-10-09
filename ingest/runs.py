"""Record each run of an ingestion job in the ingestion_runs table."""

from contextlib import contextmanager
from dataclasses import dataclass

import psycopg

START_RUN_SQL = """
INSERT INTO ingestion_runs (job, source)
VALUES (%(job)s, %(source)s)
RETURNING id;
"""

SUCCEED_RUN_SQL = """
UPDATE ingestion_runs
SET status = 'succeeded',
    finished_at = now(),
    rows_loaded = %(rows_loaded)s
WHERE id = %(run_id)s;
"""

FAIL_RUN_SQL = """
UPDATE ingestion_runs
SET status = 'failed',
    finished_at = now(),
    error = %(error)s
WHERE id = %(run_id)s;
"""


@dataclass
class Run:
    """What a job gets back from record_run, to report how many rows it wrote."""

    id: int
    rows_loaded: int = 0


@contextmanager
def record_run(conn: psycopg.Connection, job: str, source: str):
    """Record one run: 'running' at the start, then 'succeeded' or 'failed'.

    The 'running' row is committed on its own, so a run that crashes is still
    on record. The job's own writes and the 'succeeded' mark commit together.
    On any error the job's writes are rolled back and the run is marked 'failed'.
    """
    run_id = conn.execute(START_RUN_SQL, {"job": job, "source": source}).fetchone()[0]
    conn.commit()

    run = Run(run_id)
    try:
        yield run
        conn.execute(
            SUCCEED_RUN_SQL, {"run_id": run.id, "rows_loaded": run.rows_loaded}
        )
        conn.commit()
    except BaseException as exc:
        conn.rollback()
        conn.execute(
            FAIL_RUN_SQL, {"run_id": run.id, "error": f"{type(exc).__name__}: {exc}"}
        )
        conn.commit()
        raise
