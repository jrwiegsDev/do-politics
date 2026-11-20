# Operations

The routines for running DoPolitics in production: reviewing generated summaries, the monthly backup and its restore check, recovering the database, and fixing a summary job whose Anthropic key is rejected. Each command says which folder to run it from and why.

The production database, `dopolitics-db`, has an empty IP allow list: nothing on the internet can connect to it, a laptop included. Anything that reads or writes it runs inside Render, which is why review happens over SSH into the web service.

## Reviewing summaries on Render

The nightly jobs write drafts (new bills) and rewrites (approved summaries whose note asks for a change, and rejected ones). They show on the site marked "Not yet reviewed by human eyes" until approved. `review` runs on the web service, which already has the code, the installed packages and `DATABASE_URL`.

### One-time setup: an SSH key

An SSH key is a pair of files. The private key (`~/.ssh/id_ed25519`) never leaves the Mac; Render keeps only the public key (`.pub`) and uses it to check a signature only the private key can make, so no password crosses the network. The key belongs to the computer, not to Render: the same public key can be given to any service.

Run these in the Terminal app, not through an assistant's shell, since they ask for a passphrase. Any folder works: every path starts with `~`.

1. `ssh-keygen -t ed25519 -C "joe-macbook"`: press Enter for the default location, then type a passphrase twice (nothing shows while typing).
2. Let macOS remember the passphrase. Create `~/.ssh/config`:
   ```
   Host *
     AddKeysToAgent yes
     UseKeychain yes
     IdentityFile ~/.ssh/id_ed25519
   ```
   then `chmod 600 ~/.ssh/config` and `ssh-add --apple-use-keychain ~/.ssh/id_ed25519`.
3. `pbcopy < ~/.ssh/id_ed25519.pub`, then in the Render Dashboard: Account settings, **SSH Public Keys**, **Add SSH Public Key**, paste.

The first connection shows the server's fingerprint. Compare it with the Ohio line on [Render's SSH page](https://render.com/docs/ssh) before typing `yes` (on 2026-11-16 it was `SHA256:kRDsLlrHqOyqso58sEKyO6ZFMPj7p24zfNxYJ42yXGI`). Every connection then prints `client_global_hostkeys_prove_confirm: server gave bad signature for ED25519 key 0`: it comes from an optional OpenSSH feature that offers more host keys after login, is ignored by the Mac, and happens after the fingerprint check that matters. It is harmless.

### Each session

1. In Terminal, from any folder (the Render CLI finds the service by name through your login, not through files in the folder):
   ```
   render ssh dopolitics
   ```
   The prompt changes to `render@srv-...:~/project/src$`. Everything typed now runs on production.
2. On the server, in the folder you land in, `/opt/render/project/src`, the project's code. It has to be this folder, since `uv` looks there for `pyproject.toml` and `python -m ingest.summaries` for the `ingest` package:
   ```
   TZ=America/Chicago uv run --no-sync python -m ingest.summaries review
   ```
   - `TZ=America/Chicago` shows times in Central; Render runs on UTC.
   - `--no-sync` uses the packages Render installed. Without it, `uv` would try to install the development tools (pytest, Playwright) on the server.
   - Add `--previous` to first walk the last 7 days of decisions and undo any.
3. `exit` to return to the Mac.

Rewrites come first, then drafts, with `[1 of N]` showing how many are waiting. `a` approves, `r` rejects, `s` skips, `q` stops; after `a` or `r`, a note or Enter for none. Each decision is saved as it is made.

| Decision | What happens |
|---|---|
| Approve | Live at once, with its "How this does you" callout. |
| Approve with a note | Live at once; the 10:45 UTC `revise-summaries` job rewrites it to address the note, and the rewrite comes first in the next review with its changes marked. |
| Reject a draft | Never shown. When nothing approved stands for the bill, the next `revise-summaries` run redrafts it, given the rejected text and the note, if any. |
| Reject a revision | The approved text stays. With a note, the next run tries the revision again; with no note, nothing more happens. |

A deploy (any merged pull request) closes every SSH session. If a session drops, wait for the deploy to finish, connect again and run `review` again: it picks up with whatever is still a draft, and at most the draft on screen was undecided.

`render ssh dopolitics --ephemeral` starts a separate copy of the service from the latest build instead: it serves no traffic, is deleted on `exit`, and is billed for the connection time at the service's rate. It talks to the same database, so decisions made there go live too. Use it if deploys keep closing sessions.

Use the shell only for what is planned. It can run anything the app can, including the loaders and migrations.

## Monthly backup

Two layers, chosen on 2026-11-14:

- **Render's point-in-time recovery**, 7 days on the Pro workspace plan: Render backs up continuously and can restore the database as it was at any minute in that window, into a new database (see [Recovering the database](#recovering-the-database)).
- **A monthly export to the Mac**, kept outside iCloud: a full logical copy (the schema and every row, as `pg_dump` writes them) on a second machine, for a problem nobody notices for longer than 7 days, or a problem with Render itself.

The review decisions are the one thing in the database that cannot be rebuilt from public sources, which is what the export protects.

Done first on 2026-11-16 (`2026-11-16T21_10Z.dir.tar.gz`, 132 MB); repeat in the first week of each month.

### Make the export

1. Render Dashboard: project **dopolitics**, **production**, database **dopolitics-db**, **Recovery** in its menu.
2. **Create export**. It takes a few minutes and appears in the exports table; Render keeps it for 7 days. Only one export runs at a time.
3. Download it (a file named like `2026-11-16T21_10Z.dir.tar.gz`) to Downloads.

### Store it

Any folder works for these three: every path starts with `~`.

```
mkdir -p ~/Backups/dopolitics
mv ~/Downloads/*.dir.tar.gz ~/Backups/dopolitics/
ls -lh ~/Backups/dopolitics
```

`~/Backups` is used because Finder syncs Desktop and Documents to iCloud on this Mac, and the export is meant to be a copy outside it. A backup copy on an external drive as well would complete the 3-2-1 rule (three copies, on two kinds of storage, one somewhere else).

Every export is kept, for the project's whole history, until it is shut down. Each is a full copy of the database, about 130 MB in November 2026 and growing as votes and bills accumulate, so five years of monthly exports is roughly 8 GB or more. When moving to a new Mac, carry `~/Backups` across with the rest of the home folder and check that the newest export is there.

### Check that it restores

A backup that has never been restored is not known to be a backup. The check restores the export into an empty scratch database in the local Docker Postgres, which runs the same versions as Render (Postgres 18.6, PostGIS 3.6), then compares every table's row count with production. Homebrew's `pg_restore` is version 17 and cannot read a version 18 export, so the restore runs inside the container.

Run these from `~/Projects/do-politics` with Docker running: `docker compose` finds the database container through the project's `docker-compose.yml`, and `uv` and `python -m scripts.table_counts` need the project's files. Put the export's name in the `tar` line.

```
mkdir -p /tmp/dopolitics-restore
tar -xzf ~/Backups/dopolitics/2026-11-16T21_10Z.dir.tar.gz -C /tmp/dopolitics-restore
docker compose cp /tmp/dopolitics-restore/*/dopolitics db:/tmp/restore_check
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" dopolitics_restore_check'
docker compose exec db sh -c 'pg_restore -U "$POSTGRES_USER" --no-owner --no-acl --exit-on-error -d dopolitics_restore_check /tmp/restore_check'
```

- The archive holds a folder named for the export's time (with a colon, such as `2026-11-16T21:10Z`), and inside it `dopolitics`, the dump in `pg_dump`'s directory format: a table of contents, `toc.dat`, and one compressed data file per table.
- The single quotes around each `sh -c` keep `$POSTGRES_USER` from being filled in on the Mac; the container fills it in from its own settings.
- `--no-owner --no-acl` skips Render's database user and permissions, which do not exist locally. `--exit-on-error` stops at the first error instead of carrying on and printing a count of errors at the end, so exit code 0 means everything restored. On 2026-11-16 it took 3 seconds.

Then count both sides and compare:

```
POSTGRES_DB=dopolitics_restore_check uv run --env-file .env python -m scripts.table_counts > /tmp/dopolitics-restore/restored.txt
render ssh dopolitics --output text -- 'cd /opt/render/project/src && uv run --no-sync python -m scripts.table_counts' > /tmp/dopolitics-restore/production.txt
diff /tmp/dopolitics-restore/restored.txt /tmp/dopolitics-restore/production.txt
```

`POSTGRES_DB=...` in front of the command wins over the value in `.env`, so the first line counts the scratch database, not the development one. The SSH warning goes to the screen, not into the file.

The only expected difference is the last line, `postgis`: an export records that the database uses PostGIS, not PostGIS itself, so the restored copy has the version installed in the Docker image. Any other difference should be rows written after the export, such as a nightly job's run or a review decision. Do the check soon after the export, before reviewing more, so there are none. On 2026-11-16 every table matched (540 generated summaries: 212 approved, 285 drafts, 43 rejected), and PostGIS was 3.6.4 restored against 3.6.2 on Render.

The restored maps can be checked too. The Illinois State Capitol should fall in Illinois's 13th district (state code 17), once per stored Congress:

```
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d dopolitics_restore_check -c "SELECT state_fips, name FROM districts WHERE ST_Contains(boundary, ST_SetSRID(ST_MakePoint(-89.6550, 39.7983), 4269))"'
```

Clean up. This drops only the scratch database; the development database and the export in `~/Backups` are untouched:

```
docker compose exec db sh -c 'dropdb -U "$POSTGRES_USER" dopolitics_restore_check && rm -rf /tmp/restore_check'
rm -rf /tmp/dopolitics-restore
```

## Recovering the database

When production data is wrong or lost (a bad loader run, a mistaken migration), point-in-time recovery is the first choice, because it can return to any minute of the last 7 days. It never overwrites the database: it creates a new one.

1. Render Dashboard: **dopolitics-db**, **Recovery**, **Point-in-Time Recovery**, **Restore Database**.
2. Give the new database a name and choose the moment to return to (not within the last ten minutes). Copying the existing settings keeps the empty IP allow list.
3. Check the new database before switching anything to it.
4. Point the web service and the cron jobs at the new database. They get `DATABASE_URL` from `render.yaml` (`fromDatabase: dopolitics-db`), so this is a change to the Blueprint and to environment settings, made deliberately and reviewed like any other.
5. Then suspend or delete the old database.

For anything older than 7 days, the newest monthly export is the source: restore it into a new, empty database. Render's restore commands drop and recreate what they restore, so never restore an export into a database that holds data you want to keep.

## When a summary job fails with `401 invalid x-api-key`

`draft-new-summaries` and `revise-summaries` show **Failed run** in the Render Dashboard, and the job's **Logs** end with:

```
anthropic.AuthenticationError: Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}, ...}
```

Anthropic did not recognize the key. The variable is set (a missing one fails earlier, with "Could not resolve authentication method"), and it is not billing (an empty balance is a 400 saying the credit balance is too low). The first call a job makes is a free token count, so nothing was spent and nothing was written; the same bills are picked up on the next good run.

A job only uses the key on a night with work to send, so a bad key can sit unnoticed until then: on 2026-11-16 both jobs succeeded with nothing to do, and the first night with work failed. The cause that time was a cut-off paste of the right key.

1. Render Dashboard: the **dopolitics** project, **production**, **Env Groups**, **anthropic-api**.
2. Compare the end of `ANTHROPIC_API_KEY` with the key's hint on the [API keys](https://platform.claude.com/settings/keys) page of the Claude Console. If it is cut short or has a space or quote around it, paste the whole key again. If the key was deleted or disabled there, create a new one and paste that. Don't print the key in a terminal or a log to compare it.
3. Save with the option that deploys, so both jobs pick up the new value.
4. Open each job and click **Trigger Run**, `draft-new-summaries` first. Each stops at its $5 budget.
5. Check each run's **Logs** for the bills drafted and a `Spent $` line.

## When a loader fails with a timeout or a dropped connection

A loader job shows **Failed run**, and its **Logs** end with a traceback through `ingest/fetch.py` and one of:

```
TimeoutError: The read operation timed out
urllib.error.URLError: <urlopen error ...>
ConnectionResetError: [Errno 104] Connection reset by peer
http.client.IncompleteRead: IncompleteRead(... bytes read, ... more expected)
```

The source stopped answering. `read_url()` tries every download four times over about 8 seconds, so this means it failed all four: the source was down or overloaded for a while, not for one request. Above the traceback are three `WARNING ingest.fetch: ... trying again` lines for the same address. If they are missing, the failure escaped the retry, which is a bug in `ingest/fetch.py` to fix before anything else: on 2026-11-18 a Congress.gov answer stalled after its headers, the body was read outside the retry, and the bills run stopped after 2,119 bills with no warning lines.

Nothing saved is lost. The bills, votes and photo loaders commit each record as soon as it is saved, and the next run carries on from where this one stopped. The members and officials loaders write all or nothing, so they changed nothing.

1. Check the source is back: [Congress.gov API status](https://api.congress.gov/), clerk.house.gov or senate.gov in a browser.
2. Query how far the run got. In the **dopolitics** web service's **Shell**:

   ```
   uv run --no-sync python - <<'EOF'
   import os
   import psycopg

   with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
       conn.read_only = True
       for row in conn.execute(
           "SELECT job, status, rows_loaded, started_at, finished_at, error"
           " FROM ingestion_runs ORDER BY started_at DESC LIMIT 5"
       ):
           print(row)
   EOF
   ```

   `rows_loaded` on a failed run is the rows it kept.
3. Leave it for the next scheduled run, or open the job and click **Trigger Run** to catch up now. A rerun does not repeat the work already saved.

## When load-bills fails with "the bill lists N actions, but Congress.gov sent M"

`load-bills` shows **Failed run**, and its **Logs** end with:

```
ValueError: Could not read 2 bills: H.R. 2785 (119th Congress): ValueError: the bill lists 8 actions, but Congress.gov sent 5; ...
```

The run went to the end first: every other bill was saved. A bill's record counts its actions and summaries, and a list shorter than the count is never stored, so part of a history can't pass for all of it.

Right after a bill changes, Congress.gov can update the record before its lists catch up. The loader allows for that: a bill that changed less than 24 hours before it was read (`SETTLING_TIME` in `ingest/bills.py`) is not a failure. It is listed at the end of the logs under a heading such as `1 bill skipped until the next run (changed on Congress.gov in the last 24 hours, still being updated there):`, and the run succeeds. On 2026-11-20, before this was added, H.R. 2785 and H.R. 8276 failed a run this way 25 minutes after a committee markup.

So this failure means a bill that changed more than a day ago still disagrees with itself, or the loader's paging cut a list short.

1. Fetch the bill and its list from your Mac, read-only, from the project folder:

   ```
   set -a; source .env; set +a
   uv run --no-sync python - <<'EOF'
   from ingest.bills import fetch_json

   path = "bill/119/hr/2785"
   bill = fetch_json(path)["bill"]
   actions = fetch_json(f"{path}/actions", {"limit": 250})
   print(bill["updateDate"], bill["actions"]["count"], len(actions["actions"]), actions["pagination"])
   EOF
   ```

2. If the count and the list now agree, Congress.gov caught up; the next run saves the bill. If the list's own `pagination` count is higher than what was sent, the paging in `read_actions` is at fault: fix that first. If they still disagree a day or more after `updateDate`, it is Congress.gov's data: leave it, and it is named in every run's failure until they fix it.
