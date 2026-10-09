# DoPolitics

[![CI](https://github.com/jrwiegsDev/do-politics/actions/workflows/ci.yml/badge.svg)](https://github.com/jrwiegsDev/do-politics/actions/workflows/ci.yml)

People say "I don't do politics." Politics does all of us.

DoPolitics takes a street address and returns every federal official who represents it: two U.S. senators and one U.S. representative. For each official it shows recent votes, summarized in plain language, and every way to contact them. There's no signup, no account and no donation ask.

**Status:** early development. The local database (Postgres 18 + PostGIS in Docker) is running, and the district lookup works against all 441 congressional districts (435 voting seats, plus the delegates for DC and the five territories), which a repeatable Python loader pulls from the Census. The schema is managed with Alembic migrations, and every ingestion run is recorded. The lookup is a tested Python function, and GitHub Actions runs the tests against a database built from scratch on every push. A second loader pulls the sitting members of Congress and their DC office contacts from the congress-legislators project. A FastAPI endpoint ties these together: it takes a street address, geocodes it, and returns the district, both senators and the representative, or says the House seat is vacant. A third loader keeps a record of everyone who has ever served in Congress, and a fourth loads every roll call vote of the current Congress from the House Clerk and the Senate, with each member's position. No web app yet, and the API does not serve votes yet.

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
| API | Python, FastAPI, Uvicorn, httpx2 |
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

6. Load the current members of Congress. This is one small download and takes a few seconds.

   ```
   uv run --env-file .env python -m ingest.officials
   ```

7. Load everyone who has ever served in Congress. This downloads a larger file (about 13 MB) and takes a few seconds longer.

   ```
   uv run --env-file .env python -m ingest.members
   ```

8. Load the roll call votes, one chamber and session at a time. This is one download per vote with a short pause between them, so a full session takes several minutes. Add `--limit 3` to try it on a few votes first.

   ```
   uv run --env-file .env python -m ingest.votes --chamber house --session 2
   uv run --env-file .env python -m ingest.votes --chamber senate --session 2
   ```

9. Start the API. `--reload` restarts it when a file changes.

   ```
   uv run --env-file .env uvicorn app.main:app --reload
   ```

   Interactive documentation is then at `http://127.0.0.1:8000/docs`.

The database listens on `127.0.0.1:5433` by default, so it doesn't collide with a Postgres already running on 5432. Change `POSTGRES_PORT` in `.env` to use another port.

The image is built from `db/Dockerfile`: the official `postgres:18` image plus the PostGIS packages. The official `postgis/postgis` image has no arm64 build, so building our own keeps it native on Apple silicon.

To stop the database, run `docker compose down`. Data is kept in a named volume. To delete the data as well, run `docker compose down -v`.

## Schema migrations

The schema is managed with [Alembic](https://alembic.sqlalchemy.org/). Each change is a numbered file in `migrations/versions/`, written as plain SQL. Alembic records the latest revision applied in an `alembic_version` table and runs only the files after it, so the same command builds an empty database or brings an existing one up to date:

```
uv run --env-file .env alembic upgrade head
```

Each migration runs in a transaction, so a failure leaves the schema unchanged. Alembic connects through the same `ingest/db.py` function as the ingestion jobs, so no database URL or password is kept in `alembic.ini`.

To add a change, create the next file with `uv run alembic revision -m "describe the change" --rev-id 0006`, write the SQL in `upgrade()` and its reverse in `downgrade()`. A migration that has been pushed is never edited. A correction goes in a new one.

The only SQL outside the migrations is `db/init/001_extensions.sql`, which enables PostGIS when the local container is first created so its health check can pass.

## District lookup

The `districts` table (`migrations/versions/0001_baseline.py`) holds one row per district, with its boundary as a PostGIS `MultiPolygon` in NAD83 (SRID 4269), the coordinate system the Census publishes in. Each row has `effective_from` and `effective_to` dates so a redrawn map can sit alongside the one it replaces.

Finding the district for a point is one query. Longitude comes first.

```sql
SELECT state_fips, code, name
FROM districts
WHERE ST_Contains(boundary, ST_Point(:longitude, :latitude, 4269))
  AND effective_from <= :on_date
  AND (effective_to IS NULL OR effective_to > :on_date);
```

The query lives in `find_district` in `app/districts.py`. It takes the date as a parameter, which defaults to today, so the tests can check the date filter against a fixed day.

The two date conditions keep the lookup on the map in force on that date. A redrawn map can be loaded ahead of time with a future `effective_from`, and the lookup switches to it on that date with no code change or deploy.

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

## Officials

The `officials` table (`migrations/versions/0003_officials.py`) holds one row per sitting member of Congress: name, party, chamber, state, term dates and DC office contacts (phone, address, website, and a contact form where one is published). A vacant seat has no row, so the app can say a seat is vacant instead of showing a placeholder.

`ingest/officials.py` loads it from [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators):

```
uv run --env-file .env python -m ingest.officials
```

The source and the Census describe a seat differently, and the loader translates once at load time so a district joins to its representative on a plain equality match. `IL` becomes FIPS `17`, and district `13` becomes the text `13`. The source uses district `0` for two cases, which become `00` for a state with a single at-large seat and `98` for the six non-voting seats, matching the Census codes.

The load is idempotent, keyed on the member's Bioguide ID, which stays the same if a member moves from the House to the Senate. Members who are no longer in the file are removed in the same transaction. A download with fewer than 500 members is treated as broken and refused, since loading it would remove everyone it leaves out. Each run is recorded in `ingestion_runs`. Add `--dry-run` to print the rows without writing anything.

### Looking up a seat

`find_officials` in `app/officials.py` returns the sitting members for a state and district: the senators first, then the representative. It filters on term dates the same way the district lookup filters on map dates, so a member whose term has ended is left out even if the data hasn't been refreshed. The end date is exclusive, so a term ending on January 3 and its successor starting that day never both match.

## Members

`officials` answers "who holds this seat today", and it drops a member the day they leave office. Votes are cast by people, and a member's votes outlast their seat. The `members` table (`migrations/versions/0004_members.py`) is the record of the people: one row for everyone who has ever served in Congress, back to 1789, and nobody is ever removed from it.

`ingest/members.py` loads it from the same project, reading both the current and the historical file:

```
uv run --env-file .env python -m ingest.members
```

Each row holds the member's Bioguide ID, their name, and their Senate LIS ID if they have served there. The House publishes its votes by Bioguide ID and the Senate by LIS ID, so this table is what lets a Senate vote be tied to the same person as everything else. The LIS ID is unique, so two people can never share one.

The table holds everyone, not only the current Congress, because a chamber's roll can name someone with no term in it: the opening roll call of the 119th Congress lists a member-elect who never took his seat. Loading the whole history means a vote can always be tied to a known person, and it is the groundwork for looking up any past member's record.

The load is idempotent, keyed on the Bioguide ID. When a member leaves office the source moves them from the current file to the historical one, and the next run updates their row to match. A read with fewer than 12,000 people is treated as broken and refused. Each run is recorded in `ingestion_runs`. Add `--dry-run` to print the rows without writing anything.

## Votes

Two tables (`migrations/versions/0005_votes.py`) hold the roll calls. `votes` has one row per roll call: chamber, Congress, session, roll number, when it happened, the question, the bill or nomination it concerned, and the result. `vote_positions` has one row per member per roll call, with the member's position and their party and state as recorded that day.

`ingest/votes.py` loads them from the chambers' own records, the [House Clerk](https://clerk.house.gov/) and [Senate.gov](https://www.senate.gov/), which publish one XML file per roll call:

```
uv run --env-file .env python -m ingest.votes --chamber house --session 2
uv run --env-file .env python -m ingest.votes --chamber senate --session 1
```

Every roll call is loaded, including quorum calls and procedural votes, and each position is stored exactly as the chamber spells it. That is `Yea` and `Nay` on some votes, `Aye` and `No` on others, `Present`, `Not Voting`, and in the election of the Speaker a candidate's name. Deciding which votes to show is left to the code that reads them.

The two chambers publish different formats, so each has its own parser, and both produce the same rows. The differences they absorb:

- **Member IDs.** The House identifies members by Bioguide ID and the Senate by its own LIS ID. Senate positions are translated through the `members` table, and an ID that can't be translated fails the run instead of dropping a senator's vote.
- **Times.** Both publish Eastern time in different layouts. Each is converted and stored as an exact moment.
- **Finding new votes.** The Senate publishes a list of each session's votes. The House does not publish one a program can rely on, so the loader asks for the roll call after the last one it has, then the next, until one is missing. The Clerk answers a missing roll call with a normal success status and a short error document, so the loader checks the document itself, not the status.

A session is filed under the year it starts in, and a vote cast in the first days of the following January still belongs to it.

Each position must belong to a row in `members` and each belongs to a row in `votes`, enforced by foreign keys, so run `ingest.members` first. A vote and its positions are committed together as soon as they are saved. A run that fails partway is recorded as failed in `ingestion_runs` and keeps what it had saved, and the next run carries on from there. The loader pauses between requests and identifies itself to the source.

## API

The API is a FastAPI app in `app/main.py`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Confirms the API is up and can reach the database |
| `POST` | `/lookup` | Takes a street address, returns the district and its members of Congress |

```
curl -X POST http://127.0.0.1:8000/lookup \
  -H 'Content-Type: application/json' \
  -d '{"address": "401 S 2nd St, Springfield, IL 62701"}'
```

```json
{
  "district": {"state_fips": "17", "code": "13", "name": "Congressional District 13"},
  "senators": [{"name": "...", "party": "...", "phone": "...", "website": "..."}],
  "representative": {"name": "...", "party": "...", "phone": "...", "website": "..."}
}
```

A request runs three steps: `geocode` in `app/geocoder.py` asks the Census Geocoder for the address's coordinates, `find_district` finds the district containing that point, and `find_officials` returns who holds its seats. `lookup` in `app/lookup.py` joins the last two.

| Outcome | Response |
|---|---|
| Address resolved | `200` with the district, senators and representative |
| House seat is vacant | `200` with `"representative": null` |
| DC or a territory | `200` with an empty `senators` list |
| Address not found, or outside every district | `404` |
| Address missing, too short or too long | `422` |
| Census Geocoder unreachable | `503` |

The lookup is a `POST` with the address in the request body on purpose. A `GET` would put the address in the URL, and URLs are written to access logs by web servers and proxies. The HTTP client's own request logging is turned down for the same reason, and a geocoder failure is reported by its type only, because the full error message contains the request URL.

## Tests

```
uv run --env-file .env pytest
```

The tests need the database container running. They never touch the development database: the setup in `tests/conftest.py` creates a separate `dopolitics_test` database, builds its schema with the Alembic migrations, loads Illinois's districts with the real loader, and drops the database when the run finishes. Each run therefore also checks that the migrations build a working schema from nothing. The officials and members tests use made-up members rather than the live files, so they don't fail when a real member leaves office. The votes tests use made-up roll calls in each chamber's real layout. The geocoder and API tests replace the call to the Census with canned answers, so no test depends on the network or sends an address anywhere.

GitHub Actions runs the same steps on every push and pull request (`.github/workflows/ci.yml`), using the same Docker image as local development.

## Data sources

| Source | Used for |
|---|---|
| [Census Geocoder](https://geocoding.geo.census.gov/) | Address to coordinates |
| [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | District boundaries |
| [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) | Current and former members, IDs, office contacts |
| [Congress.gov API](https://api.congress.gov/) | Bills, actions, official summaries |
| [House Clerk](https://clerk.house.gov/) / [Senate.gov](https://www.senate.gov/) | Roll-call votes |
