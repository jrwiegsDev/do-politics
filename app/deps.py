"""What the JSON endpoints and the pages both depend on: a database connection from the pool."""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, Request
from psycopg_pool import ConnectionPool

from ingest.db import conninfo

# The pool keeps between these many connections open and lends them out. Every
# route is a plain def, so FastAPI runs requests on up to 40 threads at once;
# keeping the pool well under that means a busy moment makes a request wait
# briefly for a free connection instead of opening more than Postgres allows.
POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 10


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open the pool when the app starts and close it when the app stops."""
    pool = ConnectionPool(
        conninfo(),
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        open=False,
        # Before lending a connection, check that it still works, so one the
        # database dropped while it sat idle is replaced instead of failing.
        check=ConnectionPool.check_connection,
    )
    pool.open(wait=True)
    app.state.pool = pool
    yield
    pool.close()


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """Borrow a connection from the pool for one request and give it back afterwards."""
    with request.app.state.pool.connection() as conn:
        yield conn


# An endpoint asks for a database connection by giving a parameter this type.
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
