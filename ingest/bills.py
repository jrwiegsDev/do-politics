"""Load the bills that stored votes name, with their summaries and actions, from Congress.gov.

Run ingest.votes first: the bills to load are the ones its votes name. Run
ingest.members before that: every bill's sponsor must be a known member.
The key for the API is read from CONGRESS_API_KEY.

Usage:
    uv run --env-file .env python -m ingest.bills
    uv run --env-file .env python -m ingest.bills --limit 3

A vote names its bill in its issue text. The House writes 'H R 5103' and
the Senate 'H.R. 5103'. Both become the type and number Congress.gov files
the bill under: ('hr', 5103).

A bill's actions are every step it has taken: introduced, referred, passed
by recorded vote or by voice vote, signed. They have no identifier of their
own, so each run replaces a bill's whole list instead of matching action to
action.
"""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time
from itertools import islice
from time import sleep

from ingest.db import connect
from ingest.fetch import open_url
from ingest.plain_text import html_to_text
from ingest.runs import record_run
from ingest.user_agent import USER_AGENT

JOB = "bills"
SOURCE = "congress.gov"
API_ROOT = "https://api.congress.gov/v3"
PAUSE_SECONDS = 0.2

# The API sends 20 actions at a time unless asked for more, and 250 is the
# most it sends in one answer. Asking for 250 gets every stored bill's whole
# list in one request; a list longer than that fails the run rather than
# being cut short (see read_bill).
ACTIONS_PER_REQUEST = 250

BILL_TYPES = {"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"}

# Letters, dots and spaces, then one number: 'H J RES 42', 'S.Con.Res. 7'.
ISSUE_PATTERN = re.compile(r"([A-Za-z. ]+?)\s*(\d+)")

BILLS_NAMED_BY_VOTES_SQL = """
SELECT DISTINCT congress, bill_type, bill_number
FROM votes
WHERE bill_type IS NOT NULL
ORDER BY congress, bill_type, bill_number;
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
    or in the message of a failed request.
    """
    query = urllib.parse.urlencode({"format": "json"} | (params or {}))
    request = urllib.request.Request(
        f"{API_ROOT}/{path}?{query}",
        headers={
            "X-Api-Key": os.environ["CONGRESS_API_KEY"],
            "User-Agent": USER_AGENT,
        },
    )
    with open_url(request, timeout=30) as response:
        return json.load(response)


def to_bill_row(bill: dict) -> dict:
    """Turn one Congress.gov bill record into one row for the bills table."""
    latest = bill.get("latestAction") or {}
    latest_on = latest.get("actionDate")

    return {
        "congress": bill["congress"],
        "bill_type": bill["type"].lower(),
        "bill_number": int(bill["number"]),
        "title": bill["title"],
        "policy_area": (bill.get("policyArea") or {}).get("name"),
        "sponsor_bioguide_id": bill["sponsors"][0]["bioguideId"],
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


def to_action_rows(actions: list[dict]) -> list[dict]:
    """Turn a bill's actions, as Congress.gov lists them, into bill_actions rows.

    Congress.gov lists the newest first. The rows run oldest first, so
    position 0 is where the bill started. Rows have no bill_id yet.
    """
    rows = []
    for position, action in enumerate(reversed(actions)):
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


def read_actions(path: str, expected: int) -> list[dict]:
    """Download the actions of the bill at path and return them as rows.

    expected is how many the bill's own record says it has. Fewer means the
    list was cut short, so the run fails rather than store part of a bill's
    history as if it were all of it.
    """
    if not expected:
        return []

    actions = fetch_json(f"{path}/actions", {"limit": ACTIONS_PER_REQUEST})["actions"]
    if len(actions) < expected:
        raise ValueError(
            f"{path} has {expected} actions but only {len(actions)} were sent"
        )
    return to_action_rows(actions)


def read_bill(
    congress: int, bill_type: str, bill_number: int
) -> tuple[dict, list[dict], list[dict]]:
    """Download one bill and return its row, its summary rows and its action rows.

    The bill's own record says how many summaries and actions it has, so a
    bill with none of either costs no request for it.
    """
    path = f"bill/{congress}/{bill_type}/{bill_number}"
    bill = fetch_json(path)["bill"]
    row = to_bill_row(bill)

    asked = (congress, bill_type, bill_number)
    found = (row["congress"], row["bill_type"], row["bill_number"])
    if found != asked:
        raise ValueError(f"{path} says it is {found}; expected {asked}")

    summaries = []
    expected = (bill.get("summaries") or {}).get("count", 0)
    if expected:
        # The API sends a list 20 at a time. No bill has come near that many
        # summaries, so a short list means that has changed and pages are needed.
        summaries = fetch_json(f"{path}/summaries")["summaries"]
        if len(summaries) < expected:
            raise ValueError(
                f"{path} has {expected} summaries but only {len(summaries)} were sent"
            )

    actions = read_actions(path, (bill.get("actions") or {}).get("count", 0))

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


def load(conn, limit: int | None = None) -> tuple[int, list[str]]:
    """Load every bill a stored vote names, as one recorded run.

    Returns how many bills were loaded, and the bills Congress.gov does not
    have. Each bill is committed with its summaries and actions as soon as
    it is saved, so a run that fails partway keeps the bills already saved.
    A bill that is not found is reported and skipped, so one bad issue text
    on a vote cannot block every bill after it. Any other failure fails the
    run.
    """
    with record_run(conn, JOB, SOURCE) as run:
        named = conn.execute(BILLS_NAMED_BY_VOTES_SQL).fetchall()
        missing = []

        for congress, bill_type, bill_number in islice(named, limit):
            try:
                bill, summaries, actions = read_bill(congress, bill_type, bill_number)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                missing.append(f"{congress} {bill_type} {bill_number}")
                continue

            save(conn, bill, summaries, actions)
            conn.commit()
            run.rows_loaded += 1
            print(
                f"{bill_type} {bill_number}: "
                f"{len(summaries)} summaries, {len(actions)} actions",
                flush=True,
            )
            sleep(PAUSE_SECONDS)

    return run.rows_loaded, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="stop after this many bills")
    args = parser.parse_args()

    with connect() as conn:
        loaded, missing = load(conn, args.limit)
    print(f"{loaded} bills loaded from {SOURCE}")
    for bill in missing:
        print(f"  not found on Congress.gov: {bill}")


if __name__ == "__main__":
    main()
