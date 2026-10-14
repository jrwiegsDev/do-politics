"""What the JSON endpoints and the pages both depend on."""

from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import Depends

from ingest.db import connect


def get_conn() -> Iterator[psycopg.Connection]:
    """Open a database connection for one request and close it afterwards."""
    with connect() as conn:
        yield conn


# An endpoint asks for a database connection by giving a parameter this type.
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
