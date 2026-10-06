# DoPolitics

People say "I don't do politics." Politics does all of us.

DoPolitics takes a street address and returns every federal official who represents it: two U.S. senators and one U.S. representative. For each official it shows recent votes, summarized in plain language, and every way to contact them. There's no signup, no account and no donation ask.

**Status:** early development. The local database (Postgres 18 + PostGIS in Docker) is running, and the district lookup works against all 441 congressional districts (435 voting seats, plus the delegates for DC and the five territories), which a repeatable Python loader pulls from the Census. The schema is managed with Alembic migrations, and every ingestion run is recorded. No API or web app yet.

**Stance:** This is a project to illustrate how politics affects all of us, and that conservative representation often creates or exacerbates problems rather than solving them. It may be labeled a progressive project, but the reality is that the GOP has controlled all three branches of government from January 20, 2025 until the 2026 midterms, and no one's life has gotten better. Its data comes from official government sources, and every summary links to the record it summarizes.

## How it works

```
[Government sources] -> [Scheduled ingestion jobs] -> [Postgres + PostGIS] -> [Cache] -> [Read-only API] -> [Web app]
                                                                                 ^
                                              [Census Geocoder] -----------------+  (per request)
```

1. **Geo-resolution.** The address is geocoded to a point with the U.S. Census Geocoder. A point-in-polygon query against congressional district boundaries, loaded from Census TIGER/Line into PostGIS, then finds the district. Boundaries are stored in-house, so no third-party service decides who represents an address.
2. **Ingestion, not proxying.** Officials, contact info and votes come from government sources on a schedule. Each source has an adapter that fetches, normalizes and upserts the data, keyed on stable IDs so re-runs are safe, and every run is logged. User requests are answered from our own database. The geocoder is the only live call.
3. **Districts change.** Districts are modeled with effective dates, so a mid-decade redistricting doesn't quietly serve the wrong representative.

## Planned v1 scope

- Federal only: U.S. Senate and U.S. House
- Contact channels: DC and district office phones, contact forms, mailing addresses, website, social
- Recent roll-call votes with plain-language summaries
- State legislators and governors come after federal works end to end

## Privacy

Addresses are never stored. An address is geocoded, resolved to districts and discarded. It isn't cached, and it isn't logged.

## Summaries

Vote and bill summaries are generated. Each one is labeled as generated, links to the official source, and follows a published editorial policy. The policy will be added before summaries ship.

## Stack (planned)

| Layer | Choice |
|---|---|
| API | Python, FastAPI |
| Database | PostgreSQL + PostGIS |
| Cache | Redis |
| Migrations | Alembic, written as plain SQL |
| Ingestion | Python, psycopg, pyshp |
| Dependencies | uv |
| Local dev | Docker Compose (Postgres 18 + PostGIS 3.6) |
| Testing | pytest, Playwright end-to-end |
| CI | GitHub Actions |
| Hosting | Render |

## Local development

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) or another Docker runtime with Compose.

1. Copy the example environment file and set a password. Avoid `$` in the value, because Compose treats it as a variable reference.

   ```
   cp .env.example .env
   ```

2. Build the database image and start it in the background. The first build takes a few minutes.

   ```
   docker compose up -d --build
   ```

3. Confirm the container is healthy. The health check runs a PostGIS query, so `healthy` means the extension is enabled.

   ```
   docker compose ps
   ```

4. Create the tables. This needs [uv](https://docs.astral.sh/uv/), which installs the Python dependencies on first run.

   ```
   uv run --env-file .env alembic upgrade head
   ```

5. Load the district boundaries for every state. This downloads 56 files from the Census and takes a few minutes. Use a FIPS code such as `--state 17` to load one state.

   ```
   uv run --env-file .env python -m ingest.congressional_districts --state all
   ```

The database listens on `127.0.0.1:5433` by default, so it doesn't collide with a Postgres already running on 5432. Change `POSTGRES_PORT` in `.env` to use another port.

The image is built from `db/Dockerfile`: the official `postgres:18` image plus the PostGIS packages. The official `postgis/postgis` image has no arm64 build, so building our own keeps it native on Apple silicon.

To stop the database, run `docker compose down`. Data is kept in a named volume. To delete the data as well, run `docker compose down -v`.

## Schema migrations

The schema is managed with [Alembic](https://alembic.sqlalchemy.org/). Each change is a numbered file in `migrations/versions/`, written as plain SQL. Alembic records the latest revision applied in an `alembic_version` table and runs only the files after it, so the same command builds an empty database or brings an existing one up to date:

```
uv run --env-file .env alembic upgrade head
```

Each migration runs in a transaction, so a failure leaves the schema unchanged. Alembic connects through the same `ingest/db.py` function as the ingestion jobs, so no database URL or password is kept in `alembic.ini`.

To add a change, create the next file with `uv run alembic revision -m "describe the change" --rev-id 0003`, write the SQL in `upgrade()` and its reverse in `downgrade()`. A migration that has been pushed is never edited. A correction goes in a new one.

The only SQL outside the migrations is `db/init/001_extensions.sql`, which enables PostGIS when the local container is first created so its health check can pass.

## District lookup

The `districts` table (`migrations/versions/0001_baseline.py`) holds one row per district, with its boundary as a PostGIS `MultiPolygon` in NAD83 (SRID 4269), the coordinate system the Census publishes in. Each row has `effective_from` and `effective_to` dates so a redrawn map can sit alongside the one it replaces.

Finding the district for a point is one query. Longitude comes first.

```sql
SELECT code, name
FROM districts
WHERE ST_Contains(boundary, ST_Point(:longitude, :latitude, 4269))
  AND effective_from <= current_date
  AND (effective_to IS NULL OR effective_to > current_date);
```

The two date conditions keep the lookup on the map in force today. A redrawn map can be loaded ahead of time with a future `effective_from`, and the lookup switches to it on that date with no code change or deploy.

A GiST index on `boundary` keeps this fast. The index compares the point against each district's bounding box first, so the exact geometry check only runs on the one or two districts that could match. With all 441 districts loaded, the lookup still runs in about a millisecond.

### Loading boundaries

`ingest/congressional_districts.py` downloads a state's TIGER/Line file, reads the shapefile in Python and upserts each district. It takes a state's FIPS code, or `all` for the 50 states, DC and the five territories:

```
uv run --env-file .env python -m ingest.congressional_districts --state 17
uv run --env-file .env python -m ingest.congressional_districts --state all
```

The load is idempotent. A unique constraint on `(level, chamber, geoid, effective_from)` identifies a district on a given map, and the insert uses `ON CONFLICT ... DO UPDATE`, so a second run updates the same rows instead of adding duplicates. Add `--dry-run` to read the file and print the rows without writing anything.

The loader connects with `DATABASE_URL` if it's set, and otherwise with the `POSTGRES_*` values from `.env`.

Every load is recorded in the `ingestion_runs` table: the job, the source file, start and finish times, the number of rows written, and the error if it failed. The run is committed as `running` before any data is touched, so a crash still leaves a record. The district rows and the `succeeded` mark are committed together, and on an error the district rows are rolled back and the run is marked `failed`. With `--state all`, each state is its own run and its own transaction, so one failed download doesn't stop or undo the others.

```sql
SELECT source, status, rows_loaded, finished_at, error
FROM ingestion_runs
ORDER BY started_at DESC;
```

Each map's `effective_from` is the date the first Congress elected under it was seated: 2023-01-03 for most states, and 2025-01-03 for the five that redrew before the 2024 election (Alabama, Georgia, Louisiana, New York, North Carolina). The loader looks this up by state.

## Data sources

| Source | Used for |
|---|---|
| [Census Geocoder](https://geocoding.geo.census.gov/) | Address to coordinates |
| [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | District boundaries |
| [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) | Current members, IDs, office contacts |
| [Congress.gov API](https://api.congress.gov/) | Bills, actions, official summaries |
| [House Clerk](https://clerk.house.gov/) / [Senate.gov](https://www.senate.gov/) | Roll-call votes |
