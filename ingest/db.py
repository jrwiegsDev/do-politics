"""Database connection settings shared by the web app, the ingestion jobs and the migrations."""

import os

import psycopg
from psycopg.conninfo import make_conninfo


def conninfo() -> str:
    """Return the connection string: DATABASE_URL if set, otherwise the local Compose settings.

    It is read when called, not when this module is imported, so the tests
    can point it at their own database first.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    return make_conninfo(
        host="127.0.0.1",
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


def connect() -> psycopg.Connection:
    """Open one connection. The loaders and the migrations use this; the web app uses the pool."""
    return psycopg.connect(conninfo())
