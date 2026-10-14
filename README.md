# DoPolitics

[![CI](https://github.com/jrwiegsDev/do-politics/actions/workflows/ci.yml/badge.svg)](https://github.com/jrwiegsDev/do-politics/actions/workflows/ci.yml)

People say "I don't do politics." Politics does all of us.

DoPolitics takes a street address and returns every federal official who represents it: two U.S. senators and one U.S. representative. For each official it shows recent votes, each with the official summary of the bill it concerns, and every way to contact them. There's no signup, no account and no donation ask.

**Status:** early development. The local database (Postgres 18 + PostGIS in Docker) is running, and the district lookup works against all 441 congressional districts (435 voting seats, plus the delegates for DC and the five territories), which a repeatable Python loader pulls from the Census. The schema is managed with Alembic migrations, and every ingestion run is recorded. The lookup is a tested Python function, and GitHub Actions runs the tests against a database built from scratch on every push. A second loader pulls the sitting members of Congress and their DC office contacts from the congress-legislators project. A FastAPI endpoint ties these together: it takes a street address, geocodes it, and returns the district, both senators and the representative, or says the House seat is vacant. A third loader keeps a record of everyone who has ever served in Congress, and a fourth loads every roll call vote of the current Congress from the House Clerk and the Senate, with each member's position. A second endpoint returns a member's most recent votes. A fifth loader pulls each bill those votes name from the Congress.gov API, with its official summaries, and a third endpoint returns a bill and its summaries. The web app has its first page: enter an address and the officials for it appear. Their votes and the bills come next.

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
- Recent roll-call votes, each with the official summary of the bill it concerns
- State legislators and governors come after federal works end to end

## Privacy

Addresses are never stored. An address is geocoded, resolved to districts and discarded. It isn't cached, and it isn't logged.

## Summaries

In v1 every summary is an official one: the Congressional Research Service summary of the bill, as published on Congress.gov. Each is shown with the stage of the bill it describes, its date, and a link to the bill's page. A bill with no official summary says so. No summary is written or reworded by this project or by a language model.

Generated plain-language summaries may come later. If they do, each will be labeled as generated, link to the official source, and follow a published editorial policy.

## Stack (planned)

| Layer | Choice |
|---|---|
| API | Python, FastAPI, Uvicorn, httpx2 |
| Web app | Jinja2 templates served by the same FastAPI app, HTMX, a hand-written stylesheet |
| Database | PostgreSQL + PostGIS |
| Cache | Redis |
| Migrations | Alembic, written as plain SQL |
| Ingestion | Python, psycopg, pyshp, Beautiful Soup |
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

8. Load the roll call votes, one chamber at a time. This is one download per vote with a short pause between them, so a full session takes several minutes. Add `--limit 3` to try it on a few votes first.

   ```
   uv run --env-file .env python -m ingest.votes --chamber house
   uv run --env-file .env python -m ingest.votes --chamber senate
   ```

   With no session named, the loader works out the current one from today's date. To load an earlier session, name it with `--congress 119 --session 1`.

9. Load the bills those votes name, with their official summaries. This needs a free key from [api.congress.gov/sign-up](https://api.congress.gov/sign-up), set as `CONGRESS_API_KEY` in `.env`. It is one or two requests per bill and takes several minutes. Add `--limit 3` to try it on a few bills first.

   ```
   uv run --env-file .env python -m ingest.bills
   ```

10. Start the API. `--reload` restarts it when a file changes.

   ```
   uv run --env-file .env uvicorn app.main:app --reload
   ```

   The web app is then at `http://127.0.0.1:8000/` and the API's interactive documentation at `http://127.0.0.1:8000/docs`.

The database listens on `127.0.0.1:5433` by default, so it doesn't collide with a Postgres already running on 5432. Change `POSTGRES_PORT` in `.env` to use another port.

The image is built from `db/Dockerfile`: the official `postgres:18` image plus the PostGIS packages. The official `postgis/postgis` image has no arm64 build, so building our own keeps it native on Apple silicon.

To stop the database, run `docker compose down`. Data is kept in a named volume. To delete the data as well, run `docker compose down -v`.

## Schema migrations

The schema is managed with [Alembic](https://alembic.sqlalchemy.org/). Each change is a numbered file in `migrations/versions/`, written as plain SQL. Alembic records the latest revision applied in an `alembic_version` table and runs only the files after it, so the same command builds an empty database or brings an existing one up to date:

```
uv run --env-file .env alembic upgrade head
```

Each migration runs in a transaction, so a failure leaves the schema unchanged. Alembic connects through the same `ingest/db.py` function as the ingestion jobs, so no database URL or password is kept in `alembic.ini`.

To add a change, create the next file with `uv run alembic revision -m "describe the change" --rev-id 0008`, write the SQL in `upgrade()` and its reverse in `downgrade()`. A migration that has been pushed is never edited. A correction goes in a new one.

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
uv run --env-file .env python -m ingest.votes --chamber house
uv run --env-file .env python -m ingest.votes --chamber senate --congress 119 --session 1
```

The Congress and session are not written into the loader. `ingest/congress.py` works them out from the date: a Congress lasts two years and is seated on January 3 of an odd year, so January 1 and 2 still belong to the year before. Run without `--congress` and `--session`, the loader takes today's date, loads the session in progress, and also checks the one before it, because a vote cast in a session's last days can be published after the next has begun. When a new Congress is seated the loader moves to it without a code change.

Every roll call is loaded, including quorum calls and procedural votes, and each position is stored exactly as the chamber spells it. That is `Yea` and `Nay` on some votes, `Aye` and `No` on others, `Present`, `Not Voting`, and in the election of the Speaker a candidate's name.

Neither chamber says which votes are final and which are steps along the way, so the loader gives each vote a category, stored in a column added by `migrations/versions/0006_vote_category.py`:

| Category | What it covers |
|---|---|
| `legislation` | Final votes on a bill or resolution: passage, accepting the other chamber's version, a veto override |
| `nomination` | Senate confirmations |
| `procedural` | Everything else: amendments, cloture, motions to proceed, recommit or table, quorum calls |

The rule is `categorize` in `ingest/categories.py`. It reads the vote's question, and for two questions that cover both kinds of vote it reads the title too: a House resolution is `legislation` unless it is a rule setting the terms for debating a bill, and the Senate's catch-all "On the Motion" is `legislation` only for a motion to accept the House's version. A question wording the rule has never seen counts as `procedural`, so nothing is shown as a final vote by mistake, and the loader reports it. When a rule changes, this re-labels the stored votes without downloading anything:

```
uv run --env-file .env python -m ingest.votes --recategorize
```

The loader also records which bill a vote concerns, in two columns added by `migrations/versions/0007_bills.py`. The House writes a bill as `H R 5103` and the Senate as `H.R. 5103`; `bill_for` in `ingest/bills.py` turns both into the type and number Congress.gov files the bill under, `hr` and `5103`. A vote that names no bill, such as a nomination or a quorum call, leaves both columns empty, and a check constraint keeps them filled or empty together. The same `--recategorize` command fills these columns for stored votes.

The same migration creates the `bills` and `bill_summaries` tables, described under [Bills and summaries](#bills-and-summaries).

The two chambers publish different formats, so each has its own parser, and both produce the same rows. The differences they absorb:

- **Member IDs.** The House identifies members by Bioguide ID and the Senate by its own LIS ID. Senate positions are translated through the `members` table, and an ID that can't be translated fails the run instead of dropping a senator's vote.
- **Times.** Both publish Eastern time in different layouts. Each is converted and stored as an exact moment.
- **Finding new votes.** The Senate publishes a list of each session's votes. The House does not publish one a program can rely on, so the loader asks for the roll call after the last one it has, then the next, until one is missing. The Clerk answers a missing roll call with a normal success status and a short error document, so the loader checks the document itself, not the status.

A session is filed under the year it starts in, and a vote cast in the first days of the following January still belongs to it.

Each position must belong to a row in `members` and each belongs to a row in `votes`, enforced by foreign keys, so run `ingest.members` first. A vote and its positions are committed together as soon as they are saved. A run that fails partway is recorded as failed in `ingestion_runs` and keeps what it had saved, and the next run carries on from there. The loader pauses between requests and identifies itself to the source.

## Bills and summaries

Two tables (`migrations/versions/0007_bills.py`) hold the bills. `bills` has one row per bill: its Congress, type and number, title, policy area, sponsor, the date it was introduced, its latest action, and a link to its page on Congress.gov. `bill_summaries` has one row per version of a bill's official summary. The Congressional Research Service writes a summary at each stage a bill reaches, such as "Introduced in House" or "Passed Senate", so a bill can have several, and each row records the stage and its date.

`ingest/bills.py` loads them from the [Congress.gov API](https://api.congress.gov/):

```
uv run --env-file .env python -m ingest.bills
```

The loader's list of work is the stored votes: it asks for every bill that some vote names, and nothing else. A vote is tied to its bill by Congress, type and number, the same three values on both tables, so the votes loader never has to wait for this one. A vote on a bill that has not been loaded yet simply has no bill attached.

The API key is read from `CONGRESS_API_KEY` and sent in a request header, never in the address, so it cannot end up in a logged URL or in a recorded error.

Summaries are published as HTML. `html_to_text` in `ingest/plain_text.py` converts each one to plain text with [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) at load time: a paragraph becomes a block set apart by a blank line, a list item becomes a line starting with a dash, and bold, italics and link addresses are dropped. The API serves only that text, so the web app never has to display markup written by another site. The original HTML is stored beside it and never served, so the conversion can be improved and re-applied later without downloading anything again.

Some bills have no official summary, and some never get one. That is stored as a bill with no summary rows, and the API returns an empty list for it.

The load is idempotent: a bill is keyed on its Congress, type and number, and a summary on its bill and version. Each bill is committed with its summaries as soon as it is saved, so a run that fails partway keeps what it had. A bill Congress.gov does not have is reported and skipped; any other failure, such as a refused key, fails the run. Each run is recorded in `ingestion_runs`. Every sponsor must be a row in `members`, so run `ingest.members` first.

## API

The API is a FastAPI app in `app/main.py`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Confirms the API is up and can reach the database |
| `POST` | `/lookup` | Takes a street address, returns the district and its members of Congress |
| `GET` | `/members/{bioguide_id}/votes` | Returns a member's most recent votes, newest first |
| `GET` | `/bills/{congress}/{bill_type}/{bill_number}` | Returns a bill and its official summaries |

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

### A member's votes

```
curl "http://127.0.0.1:8000/members/B001315/votes?limit=2"
```

```json
{
  "member": {"bioguide_id": "B001315", "name": "Nikki Budzinski"},
  "votes": [
    {
      "chamber": "house", "congress": 119, "session": 2, "roll_number": 314,
      "voted_at": "2026-09-16T23:05:00Z",
      "question": "On Motion to Suspend the Rules and Pass",
      "issue": "S 2403", "title": "Retire through Ownership Act",
      "result": "Passed", "category": "legislation",
      "source_url": "https://clerk.house.gov/evs/2026/roll314.xml",
      "position": "Yea",
      "bill": {
        "congress": 119, "bill_type": "s", "bill_number": 2403,
        "title": "Retire through Ownership Act",
        "policy_area": "...",
        "source_url": "https://www.congress.gov/bill/119th-congress/senate-bill/2403"
      }
    }
  ]
}
```

`find_recent_votes` in `app/votes.py` joins `vote_positions` to `votes` for one member. The member's ID comes from the `/lookup` response. It is a public identifier, unlike an address, so it is part of the URL.

Each vote carries the bill it concerns, joined from `bills` with a `LEFT JOIN` so a vote with no bill is still returned. `bill` is `null` for a vote that names no bill, such as a confirmation, and for a bill that has not been loaded yet. The three values in it are the address of the bill's own endpoint.

| Parameter | Default | Meaning |
|---|---|---|
| `limit` | `20` | How many votes to return, from 1 to 100 |
| `show` | `final` | `final` returns final votes on legislation and Senate confirmations. `all` adds procedural votes |

| Outcome | Response |
|---|---|
| Member found | `200` with the member and a list of votes, which is empty if they have none |
| No member has that ID | `404` |
| Malformed ID, `limit` out of range, or unknown `show` | `422` |

Times are exact moments in UTC. Each vote links to the chamber's own record of it.

### A bill and its summaries

```
curl http://127.0.0.1:8000/bills/119/hjres/20
```

```json
{
  "congress": 119, "bill_type": "hjres", "bill_number": 20,
  "title": "Providing for congressional disapproval under chapter 8 of title 5, ...",
  "policy_area": "Energy",
  "sponsor_bioguide_id": "P000609", "sponsor_name": "...",
  "introduced_on": "2025-01-15",
  "latest_action_on": "2025-05-09", "latest_action": "Became Public Law No: 119-6.",
  "source_url": "https://www.congress.gov/bill/119th-congress/house-joint-resolution/20",
  "summaries": [
    {"version_code": "49", "action_on": "2025-05-09", "action_desc": "Public Law", "summary_text": "..."},
    {"version_code": "00", "action_on": "2025-01-15", "action_desc": "Introduced in House", "summary_text": "..."}
  ]
}
```

`find_bill` in `app/bills.py` reads the bill, then its summaries, newest stage first. A summary describes the bill at the stage it names, not at the moment of any one vote, so each carries its stage and date and the page can say which version it is showing.

The list of votes carries only a bill's title and link, and the summaries are fetched here when a bill is opened. One summary can run past 170,000 characters, so sending them with every vote in a list would make each response large for text that is mostly never read.

The summary's original HTML is never sent, and three things keep it that way: the query names its columns and leaves that one out, the endpoint declares the shape of its response so anything not named in it is dropped, and a test hands the endpoint the HTML on purpose and checks it does not come out.

| Outcome | Response |
|---|---|
| Bill found | `200` with the bill and a list of summaries, which is empty if it has none |
| Bill is not stored | `404` |
| Unknown bill type, or a Congress or number below 1 | `422` |

## Web app

The pages are built on the server by the same FastAPI app that serves the API, so there is one service to run and deploy. `app/pages.py` holds the routes, `app/templates/` the [Jinja2](https://jinja.palletsprojects.com/) templates, and `app/static/` the stylesheet and a pinned copy of [HTMX](https://htmx.org/) 4.0.0.

| Method | Path | Returns |
|---|---|---|
| `GET` | `/` | The whole page: a greeting and the address form |
| `POST` | `/tiles/officials` | The officials tile for an address, as a piece of HTML |

The page is made of tiles. Submitting the address does not load a new page: HTMX posts the form, the server answers with the HTML for one tile, and HTMX puts it into the page. The page routes call the same functions as the JSON endpoints (`geocode`, then `lookup`), so the API and the page cannot disagree. When an address can't be resolved, the tile holds a plain message and the response carries the same status code `/lookup` would give.

The privacy rules hold here too. The address travels in the body of a `POST` and is never sent back in the HTML or written to a log. The form also declares `method="post"`, because a form sends with `GET` by default and that would put the address in the URL if the script ever failed to load. The stylesheet and the script are served from this app, so opening the page asks no other site for anything.

Jinja2 escapes every value it prints, so text from a source, such as a name or a bill summary, is shown as text and can never run as HTML.

## Tests

```
uv run --env-file .env pytest
```

The tests need the database container running. They never touch the development database: the setup in `tests/conftest.py` creates a separate `dopolitics_test` database, builds its schema with the Alembic migrations, loads Illinois's districts with the real loader, and drops the database when the run finishes. Each run therefore also checks that the migrations build a working schema from nothing. The officials and members tests use made-up members rather than the live files, so they don't fail when a real member leaves office. The votes tests use made-up roll calls in each chamber's real layout, and the bills tests a made-up bill in the layout Congress.gov sends, so none needs an API key. The geocoder, API and page tests replace the call to the Census with canned answers, so no test depends on the network or sends an address anywhere.

GitHub Actions runs the same steps on every push and pull request (`.github/workflows/ci.yml`), using the same Docker image as local development.

To see which lines no test runs, add `--cov`:

```
uv run --env-file .env pytest --cov
```

This uses [pytest-cov](https://pytest-cov.readthedocs.io/) and prints, for each file in `app/` and `ingest/`, the share of lines the tests ran and the line numbers they missed. CI runs it this way and fails if the total drops below the floor set in `pyproject.toml`. The floor sits just under the measured figure, so coverage cannot slide unnoticed; it is not a target. What is left untested on purpose is the downloading itself and the command-line entry points of the districts, officials, members and bills loaders. The votes loader's entry point is tested, because it enforces rules about which arguments go together.

## Code checks

[Ruff](https://docs.astral.sh/ruff/) checks the code without running it. As a linter it flags likely mistakes, such as a function defined twice or an import that is never used. As a formatter it keeps the layout consistent, so a diff shows what changed and not how someone wrapped a line.

```
uv run ruff check .
uv run ruff format .
```

`ruff check --fix .` applies the fixes it can make by itself. Migrations that have been pushed are excluded, since they are never edited.

[ty](https://docs.astral.sh/ty/) is a type checker. It also reads the code without running it, and follows each value from where it is made to where it is used. Its commonest finding is a value that might be `None` being used as if it never were, such as an XML element a parser expects that a source file might not contain.

```
uv run ty check
```

ty is in beta, so its exact version is pinned in `uv.lock` and upgraded on purpose.

CI runs all three checks on every push and pull request, and fails if any of them finds something.

The same three checks can run before every commit, through [pre-commit](https://pre-commit.com/). Set it up once per clone:

```
uv run pre-commit install
```

After that, `git commit` runs the checks first and stops if one fails, so a mistake is caught in about a second and not after a push. The hooks are listed in `.pre-commit-config.yaml`. Each one calls the tool through uv, so the hook, CI and the terminal all use the version pinned in `uv.lock`. `uv run pre-commit run --all-files` runs them without committing.

## Dependency updates

[Dependabot](https://docs.github.com/en/code-security/dependabot) checks once a week for newer versions of the Python libraries, the GitHub Actions used in CI, and the Postgres base image, and opens a pull request for each update (`.github/dependabot.yml`). CI runs on those pull requests like any other. A release has to be a week old before it is proposed, so the project is never among the first to install a bad one.

## Data sources

| Source | Used for |
|---|---|
| [Census Geocoder](https://geocoding.geo.census.gov/) | Address to coordinates |
| [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | District boundaries |
| [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) | Current and former members, IDs, office contacts |
| [Congress.gov API](https://api.congress.gov/) | Bills, actions, official summaries |
| [House Clerk](https://clerk.house.gov/) / [Senate.gov](https://www.senate.gov/) | Roll-call votes |
