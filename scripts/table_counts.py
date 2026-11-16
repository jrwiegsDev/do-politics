"""Print every table's row count, for checking that a restored backup matches production.

Read-only: it opens the connection in a read-only transaction. It prints one
"name count" line per table, sorted, plus the review decisions by status, the
newest decision, and the schema's migration, so two runs can be compared with
diff. The PostGIS version is printed last and on its own, since a restore
takes the version installed where it lands, not the one in the backup.

Run it as a module, from the project folder, so the ingest package can be
imported, as the loaders are run.

On a restored copy in the local Docker database:
    POSTGRES_DB=dopolitics_restore_check uv run --env-file .env python -m scripts.table_counts

On production, over SSH into the web service (DATABASE_URL is already set there):
    render ssh dopolitics --output text -- 'cd /opt/render/project/src && uv run --no-sync python -m scripts.table_counts'

See docs/operations.md.
"""

from psycopg import sql

from ingest.db import connect

TABLES_SQL = """
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
"""


def main() -> None:
    with connect() as conn:
        conn.read_only = True
        for (table,) in conn.execute(TABLES_SQL).fetchall():
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            row = conn.execute(query).fetchone()
            print(f"{table} {row[0] if row else 0}")
        decisions = conn.execute(
            "SELECT status, count(*) FROM generated_summaries GROUP BY status ORDER BY status"
        ).fetchall()
        for status, count in decisions:
            print(f"generated_summaries.{status} {count}")
        newest = conn.execute(
            "SELECT max(reviewed_at) AT TIME ZONE 'UTC' FROM generated_summaries"
        ).fetchone()
        print(f"newest_decision_utc {newest[0] if newest else None}")
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        print(f"migration {version[0] if version else None}")
        postgis = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'postgis'"
        ).fetchone()
        print(
            f"\npostgis {postgis[0] if postgis else None} (may differ after a restore)"
        )


if __name__ == "__main__":
    main()
