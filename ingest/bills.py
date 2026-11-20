"""Load every bill of the 119th Congress onward, with its summaries and actions, from Congress.gov.

Run ingest.members first: every bill's sponsor must be a known member. Run
ingest.votes before this too, so a bill a new vote names is loaded the same
night. The key for the API is read from CONGRESS_API_KEY.

Usage:
    uv run --env-file .env python -m ingest.bills
    uv run --env-file .env python -m ingest.bills --limit 3

Each run lists every bill of each Congress from FIRST_STORED_CONGRESS to the
current one, 250 to a request (80 requests for a Congress of 20,000 bills),
and downloads only the bills that are new or have changed. The list gives the
day a bill last changed, not the time, so a stored bill is downloaded again
when it changed on or after the day before it was last downloaded: once or
twice for each change, then not again until it changes. A bill a stored vote
names is downloaded first if it is not stored yet, listed or not. The 120th
Congress, seated on January 3, 2027, is picked up with no change here.

--limit caps how many bills one run downloads, the most recently changed
first. When this began storing every bill, about 19,500 bills of the 119th
were not stored, so the nightly job runs with --limit 8000 and works through
them over three nights; after that a night has a few hundred.

A bill whose sponsor is not in the members table yet, such as a new member's
first bill before the weekly members run, is skipped and reported. It is
still not stored, so the next run tries it again.

A vote names its bill in its issue text. The House writes 'H R 5103' and
the Senate 'H.R. 5103'. Both become the type and number Congress.gov files
the bill under: ('hr', 5103).

A bill's actions are every step it has taken: introduced, referred, passed
by recorded vote or by voice vote, signed. They have no identifier of their
own, so each run replaces a bill's whole list instead of matching action to
action.

A bill's record counts its summaries and actions, and a list shorter than
the count is not stored. Right after a bill changes, Congress.gov can update
the record before the list catches up, so a bill that changed less than
SETTLING_TIME ago is left for the next run and reported, and the run still
succeeds. A short list from a bill that changed longer ago than that fails
the run, naming the bill.
"""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from itertools import islice
from time import sleep

from ingest.congress import congresses_since, today_in_washington
from ingest.db import connect
from ingest.fetch import read_url
from ingest.logs import configure_logging
from ingest.plain_text import html_to_text
from ingest.runs import record_run
from ingest.user_agent import USER_AGENT

JOB = "bills"
SOURCE = "congress.gov"
API_ROOT = "https://api.congress.gov/v3"

# The first Congress whose every bill is stored. Bills of earlier Congresses
# are loaded only if a stored vote names one.
FIRST_STORED_CONGRESS = 119

# A pause after every request keeps a run under 14,400 requests an hour,
# inside Congress.gov's limit of 20,000, however quickly the answers come.
PAUSE_SECONDS = 0.25

# The API sends 20 items at a time unless asked for more, and 250 is the most
# it sends in one answer: bills in a list, a bill's summaries or its actions.
MOST_PER_REQUEST = 250

# How long Congress.gov may take to bring a changed bill's lists in line with
# its record. On 2026-11-20 the record of H.R. 2785 counted 8 actions 25
# minutes after it changed, while its list of actions still sent the old 5.
SETTLING_TIME = timedelta(hours=24)

BILL_TYPES = {"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"}

# Letters, dots and spaces, then one number: 'H J RES 42', 'S.Con.Res. 7'.
ISSUE_PATTERN = re.compile(r"([A-Za-z. ]+?)\s*(\d+)")

# Bills a stored vote names that are not stored yet. A stored one is kept up
# to date by the list of every bill, like any other.
UNSTORED_VOTED_BILLS_SQL = """
SELECT DISTINCT v.congress, v.bill_type, v.bill_number
FROM votes v
WHERE v.bill_type IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM bills b
      WHERE b.congress = v.congress
        AND b.bill_type = v.bill_type
        AND b.bill_number = v.bill_number
  )
ORDER BY v.congress, v.bill_type, v.bill_number;
"""

# The day each stored bill of a Congress was last downloaded, in UTC
# (updated_at is set on every save). Congress.gov's list may count days on
# Eastern time instead; the day of overlap in bills_to_download covers that.
DOWNLOADED_ON_SQL = """
SELECT bill_type, bill_number, (updated_at AT TIME ZONE 'UTC')::date
FROM bills
WHERE congress = %(congress)s;
"""

MEMBER_IDS_SQL = """
SELECT bioguide_id FROM members;
"""

UPSERT_BILL_SQL = """
INSERT INTO bills (
    congress, bill_type, bill_number, title, policy_area, sponsor_bioguide_id,
    introduced_on, latest_action_on, latest_action, source_url, source_updated_at
)
VALUES (
    %(congress)s, %(bill_type)s, %(bill_number)s, %(title)s, %(policy_area)s, %(sponsor_bioguide_id)s,
    %(introduced_on)s, %(latest_action_on)s, %(latest_action)s, %(source_url)s, %(source_updated_at)s
)
ON CONFLICT (congress, bill_type, bill_number)
DO UPDATE SET
    title = EXCLUDED.title,
    policy_area = EXCLUDED.policy_area,
    sponsor_bioguide_id = EXCLUDED.sponsor_bioguide_id,
    introduced_on = EXCLUDED.introduced_on,
    latest_action_on = EXCLUDED.latest_action_on,
    latest_action = EXCLUDED.latest_action,
    source_url = EXCLUDED.source_url,
    source_updated_at = EXCLUDED.source_updated_at,
    updated_at = now()
RETURNING id;
"""

UPSERT_SUMMARY_SQL = """
INSERT INTO bill_summaries (
    bill_id, version_code, action_on, action_desc,
    summary_text, summary_html_source, source_updated_at
)
VALUES (
    %(bill_id)s, %(version_code)s, %(action_on)s, %(action_desc)s,
    %(summary_text)s, %(summary_html_source)s, %(source_updated_at)s
)
ON CONFLICT (bill_id, version_code)
DO UPDATE SET
    action_on = EXCLUDED.action_on,
    action_desc = EXCLUDED.action_desc,
    summary_text = EXCLUDED.summary_text,
    summary_html_source = EXCLUDED.summary_html_source,
    source_updated_at = EXCLUDED.source_updated_at,
    updated_at = now();
"""

# A bill's actions are replaced as a set: delete what is stored, insert the
# list just read. Both run in the transaction that saves the bill, so a
# reader sees the old list or the new one, never a mix or an empty list.
DELETE_ACTIONS_SQL = """
DELETE FROM bill_actions WHERE bill_id = %(bill_id)s;
"""

INSERT_ACTION_SQL = """
INSERT INTO bill_actions (
    bill_id, position, acted_on, acted_at, action_code, action_type, source, action_text
)
VALUES (
    %(bill_id)s, %(position)s, %(acted_on)s, %(acted_at)s,
    %(action_code)s, %(action_type)s, %(source)s, %(action_text)s
);
"""


def bill_for(issue: str | None) -> tuple[str, int] | None:
    """Return (bill_type, bill_number) for a vote's issue, or None when it names no bill.

    Nominations ('PN12-1'), quorum calls and votes with no issue are not bills.
    """
    match = ISSUE_PATTERN.fullmatch((issue or "").strip())
    if match is None:
        return None

    letters, number = match.groups()
    bill_type = letters.replace(".", "").replace(" ", "").lower()
    if bill_type not in BILL_TYPES:
        return None
    return bill_type, int(number)


def fetch_json(path: str, params: dict[str, int] | None = None) -> dict:
    """Ask the Congress.gov API for one address, such as 'bill/119/hr/5103'.

    params adds to the query string, as {'limit': 250} does. The key travels
    in a header, never in the address, so it cannot end up in a logged URL
    or in the message of a failed request. Each answer is followed by a
    pause, so no run can ask faster than the API allows.
    """
    query = urllib.parse.urlencode({"format": "json"} | (params or {}))
    request = urllib.request.Request(
        f"{API_ROOT}/{path}?{query}",
        headers={
            "X-Api-Key": os.environ["CONGRESS_API_KEY"],
            "User-Agent": USER_AGENT,
        },
    )
    answer = json.loads(read_url(request, timeout=30).body)
    sleep(PAUSE_SECONDS)
    return answer


def list_bills(congress: int) -> dict[tuple[str, int], date]:
    """Return the day each bill of a Congress last changed, by (bill_type, bill_number).

    The list comes MOST_PER_REQUEST bills at a time, and the first answer
    says how many there are. The offsets are counted here rather than taken
    from each answer's link to the next page, so every request is made by
    fetch_json.
    """
    path = f"bill/{congress}"
    first = fetch_json(path, {"limit": MOST_PER_REQUEST})
    pages = [first]
    for offset in range(
        MOST_PER_REQUEST, first["pagination"]["count"], MOST_PER_REQUEST
    ):
        pages.append(fetch_json(path, {"limit": MOST_PER_REQUEST, "offset": offset}))

    return {
        (item["type"].lower(), int(item["number"])): date.fromisoformat(
            item["updateDate"][:10]
        )
        for page in pages
        for item in page["bills"]
    }


def bills_to_download(
    listed: dict[tuple[str, int], date], downloaded_on: dict[tuple[str, int], date]
) -> list[tuple[str, int]]:
    """Return the listed bills that are new or changed, the most recently changed first.

    The list gives only the day a bill changed, so a bill that changed later
    on the day it was downloaded would look unchanged. A bill is taken when
    it changed on or after the day before its last download; one that
    changed earlier than that has nothing new.
    """
    due = [
        bill
        for bill, changed in listed.items()
        if bill not in downloaded_on
        or changed >= downloaded_on[bill] - timedelta(days=1)
    ]
    return sorted(due, key=lambda bill: listed[bill], reverse=True)


def to_bill_row(bill: dict) -> dict:
    """Turn one Congress.gov bill record into one row for the bills table.

    A bill with no sponsor gets None, which load reports and skips.
    """
    latest = bill.get("latestAction") or {}
    latest_on = latest.get("actionDate")
    sponsors = bill.get("sponsors") or []

    return {
        "congress": bill["congress"],
        "bill_type": bill["type"].lower(),
        "bill_number": int(bill["number"]),
        "title": bill["title"],
        "policy_area": (bill.get("policyArea") or {}).get("name"),
        "sponsor_bioguide_id": sponsors[0]["bioguideId"] if sponsors else None,
        "introduced_on": date.fromisoformat(bill["introducedDate"]),
        "latest_action_on": date.fromisoformat(latest_on) if latest_on else None,
        "latest_action": latest.get("text"),
        "source_url": bill["legislationUrl"],
        "source_updated_at": datetime.fromisoformat(bill["updateDate"]),
    }


def to_summary_row(summary: dict) -> dict:
    """Turn one Congress.gov summary into one bill_summaries row, without its bill_id."""
    return {
        "version_code": summary["versionCode"],
        "action_on": date.fromisoformat(summary["actionDate"]),
        "action_desc": summary["actionDesc"],
        "summary_text": html_to_text(summary["text"]),
        "summary_html_source": summary["text"],
        "source_updated_at": datetime.fromisoformat(summary["updateDate"]),
    }


class NotSettledYet(Exception):
    """A bill changed so recently that its lists may not have caught up yet.

    Not a ValueError, so load can tell it apart from a record it cannot read.
    """


def short_list(
    path: str, kind: str, expected: int, sent: int, changed_at: datetime, now: datetime
) -> Exception:
    """Return the error for a list shorter than the bill's own record says.

    A bill that changed less than SETTLING_TIME before now gets NotSettledYet:
    the list may only be behind the record. An older bill gets ValueError: the
    list was cut short, and part of a history must not be stored as all of it.
    """
    message = f"{path} has {expected} {kind} but only {sent} were sent"
    if now - changed_at < SETTLING_TIME:
        return NotSettledYet(message)
    return ValueError(message)


def to_action_rows(actions: list[dict]) -> list[dict]:
    """Turn a bill's actions, as Congress.gov lists them, into bill_actions rows.

    Congress.gov lists the newest first. The rows run oldest first, so
    position 0 is where the bill started. Rows have no bill_id yet.

    An action with no words is left out. The Library of Congress sometimes
    records one step twice under two codes, and one copy can come without
    its text: S. 3347's introduction on 2025-12-04 is listed as 'Intro-S'
    with no text beside '10000', 'Introduced in Senate'. A copy with no
    words has nothing to show, so it is dropped before the rows are
    numbered, and the positions stay one after another.
    """
    worded = [action for action in actions if action.get("text")]
    rows = []
    for position, action in enumerate(reversed(worded)):
        acted_at = action.get("actionTime")
        rows.append(
            {
                "position": position,
                "acted_on": date.fromisoformat(action["actionDate"]),
                "acted_at": time.fromisoformat(acted_at) if acted_at else None,
                "action_code": action.get("actionCode"),
                "action_type": action["type"],
                "source": action["sourceSystem"]["name"],
                "action_text": action["text"],
            }
        )
    return rows


def read_actions(
    path: str, expected: int, changed_at: datetime, now: datetime
) -> list[dict]:
    """Download the actions of the bill at path and return them as rows.

    expected is how many the bill's own record says it has, and changed_at
    is when that record last changed. A list longer than MOST_PER_REQUEST,
    such as a defense authorization's, is asked for page by page. Fewer
    than expected raises the error short_list picks.
    """
    actions = []
    while len(actions) < expected:
        params = {"limit": MOST_PER_REQUEST}
        if actions:
            params["offset"] = len(actions)
        page = fetch_json(f"{path}/actions", params)["actions"]
        actions += page
        # A page shorter than asked for is the end of the list.
        if len(page) < MOST_PER_REQUEST:
            break

    if len(actions) < expected:
        raise short_list(path, "actions", expected, len(actions), changed_at, now)
    return to_action_rows(actions)


def read_bill(
    congress: int, bill_type: str, bill_number: int, now: datetime | None = None
) -> tuple[dict, list[dict], list[dict]]:
    """Download one bill and return its row, its summary rows and its action rows.

    The bill's own record says how many summaries and actions it has, so a
    bill with none of either costs no request for it. now is when the bill
    is read, the current time unless a test sets it.
    """
    if now is None:
        now = datetime.now(UTC)
    path = f"bill/{congress}/{bill_type}/{bill_number}"
    bill = fetch_json(path)["bill"]
    row = to_bill_row(bill)

    asked = (congress, bill_type, bill_number)
    found = (row["congress"], row["bill_type"], row["bill_number"])
    if found != asked:
        raise ValueError(f"{path} says it is {found}; expected {asked}")

    changed_at = row["source_updated_at"]
    summaries = []
    expected = (bill.get("summaries") or {}).get("count", 0)
    if expected:
        # No bill has come near MOST_PER_REQUEST summaries, so a short list
        # means that has changed and pages are needed.
        summaries = fetch_json(f"{path}/summaries", {"limit": MOST_PER_REQUEST})[
            "summaries"
        ]
        if len(summaries) < expected:
            raise short_list(
                path, "summaries", expected, len(summaries), changed_at, now
            )

    expected = (bill.get("actions") or {}).get("count", 0)
    actions = read_actions(path, expected, changed_at, now)

    return row, [to_summary_row(summary) for summary in summaries], actions


def save(conn, bill: dict, summaries: list[dict], actions: list[dict]) -> None:
    """Upsert one bill and its summaries, and replace its actions.

    Nothing is committed here. The caller commits once the whole bill is
    saved, so its actions are swapped in one transaction.
    """
    bill_id = conn.execute(UPSERT_BILL_SQL, bill).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            UPSERT_SUMMARY_SQL,
            [summary | {"bill_id": bill_id} for summary in summaries],
        )
        cur.execute(DELETE_ACTIONS_SQL, {"bill_id": bill_id})
        cur.executemany(
            INSERT_ACTION_SQL,
            [action | {"bill_id": bill_id} for action in actions],
        )


def bills_due(conn, today: date) -> list[tuple[int, str, int]]:
    """Return the bills to download: unstored ones a vote names, then new or changed ones.

    Votes first, since a vote row links to its bill. Then the newest
    Congress first, and within each the most recently changed first.
    """
    due = [tuple(row) for row in conn.execute(UNSTORED_VOTED_BILLS_SQL)]
    taken = set(due)
    for congress in reversed(congresses_since(FIRST_STORED_CONGRESS, today)):
        downloaded_on = {
            (bill_type, bill_number): day
            for bill_type, bill_number, day in conn.execute(
                DOWNLOADED_ON_SQL, {"congress": congress}
            )
        }
        for bill_type, bill_number in bills_to_download(
            list_bills(congress), downloaded_on
        ):
            if (congress, bill_type, bill_number) not in taken:
                due.append((congress, bill_type, bill_number))
    return due


def load(
    conn, today: date, limit: int | None = None
) -> tuple[int, list[str], list[str], list[str]]:
    """Load every new or changed bill of the stored Congresses, as one recorded run.

    Returns how many bills were saved, the bills Congress.gov does not have,
    the bills skipped because their sponsor is not a known member, and the
    bills left for the next run because they have not settled. Each bill is
    committed with its summaries and actions as soon as it is saved, so a
    run that stops partway keeps the bills already saved, and the next run
    picks up the rest. limit caps how many bills are downloaded.

    A bill that is not found, whose sponsor is not known yet, or that has
    not settled is reported and skipped; it is not saved, so the next run
    tries it again. A record that cannot be read is skipped too, so one bad
    record cannot block the bills after it, but the run is marked failed at
    the end, naming them. Any other failure fails the run at once.
    """
    with record_run(conn, JOB, SOURCE) as run:
        known = {bioguide_id for (bioguide_id,) in conn.execute(MEMBER_IDS_SQL)}
        missing, unknown_sponsor, unsettled, unreadable = [], [], [], []

        for congress, bill_type, bill_number in islice(bills_due(conn, today), limit):
            name = f"{congress} {bill_type} {bill_number}"
            try:
                bill, summaries, actions = read_bill(congress, bill_type, bill_number)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                missing.append(name)
                continue
            except NotSettledYet as error:
                unsettled.append(f"{name} ({error})")
                continue
            except (KeyError, IndexError, ValueError) as error:
                unreadable.append(f"{name}: {error!r}")
                continue

            if bill["sponsor_bioguide_id"] not in known:
                unknown_sponsor.append(f"{name} ({bill['sponsor_bioguide_id']})")
                continue

            save(conn, bill, summaries, actions)
            run.rows_loaded += 1
            run.commit(conn)
            print(
                f"{name}: {len(summaries)} summaries, {len(actions)} actions",
                flush=True,
            )

        if unreadable:
            raise ValueError(
                f"Could not read {len(unreadable)} of the bills: "
                + "; ".join(unreadable[:10])
            )

    return run.rows_loaded, missing, unknown_sponsor, unsettled


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="stop after this many bills")
    args = parser.parse_args()

    with connect() as conn:
        loaded, missing, unknown_sponsor, unsettled = load(
            conn, today_in_washington(), args.limit
        )
    print(f"{loaded} bills loaded from {SOURCE}")
    for bill in missing:
        print(f"  not found on Congress.gov: {bill}")
    for bill in unknown_sponsor:
        print(f"  sponsor not yet in members, tried again next run: {bill}")
    for bill in unsettled:
        print(f"  changed in the last day, not settled, tried again next run: {bill}")


if __name__ == "__main__":
    main()
