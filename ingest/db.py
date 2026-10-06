"""Database connection shared by the ingestion jobs and the migrations."""

import os

import psycopg


def connect() -> psycopg.Connection:
    """Connect with DATABASE_URL if set, otherwise with the local Compose settings."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg.connect(url)

    return psycopg.connect(
        host="127.0.0.1",
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )
