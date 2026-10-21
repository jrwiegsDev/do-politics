"""Made-up officials, members, votes and bills shared by the tests."""

from datetime import UTC, date, datetime, timedelta

from ingest.bills import save as save_bill
from ingest.members import UPSERT_SQL as UPSERT_MEMBER_SQL
from ingest.officials import UPSERT_SQL
from ingest.photos import UPSERT_SQL as UPSERT_PHOTO_SQL
from ingest.photos import to_row as to_photo_row
from ingest.votes import save


def official(
    bioguide_id, name, chamber, district_code=None, state="IL", state_fips="17"
):
    """Build a made-up officials row with a term from 2025-01-03 to 2027-01-03."""
    return {
        "bioguide_id": bioguide_id,
        "name": name,
        "party": "Independent",
        "chamber": chamber,
        "state": state,
        "state_fips": state_fips,
        "district_code": district_code,
        "term_start": date(2025, 1, 3),
        "term_end": date(2027, 1, 3),
        "phone": None,
        "office_address": None,
        "website": None,
        "contact_form": None,
        "source": "test",
    }


SENATOR_A = official("T000010", "Alex Senator", "senate")
SENATOR_B = official("T000011", "Blake Senator", "senate")
REP_13 = official("T000012", "Casey Rep", "house", "13")
REP_12 = official("T000013", "Drew Rep", "house", "12")
MISSOURI_SENATOR = official(
    "T000014", "Eli Senator", "senate", state="MO", state_fips="29"
)


def add(conn, *rows):
    """Insert made-up officials rows."""
    for row in rows:
        conn.execute(UPSERT_SQL, row)


def names(officials):
    """Return just the names, in the order the lookup gave them."""
    return [o["name"] for o in officials]


MEMBERS = [
    {
        "bioguide_id": "T000001",
        "lis_id": "S900",
        "name": "Pat Example",
        "source": "legislators-current",
    },
    {
        "bioguide_id": "T000002",
        "lis_id": "S901",
        "name": "Sam Sample",
        "source": "legislators-current",
    },
]


def add_members(conn) -> None:
    """Insert the two made-up members the sample votes refer to."""
    for member in MEMBERS:
        conn.execute(UPSERT_MEMBER_SQL, member)


def add_vote(
    conn,
    roll_number,
    day,
    positions,
    question="On Passage",
    chamber="house",
    title="Example Act",
    issue="H R 9999",
    session=2,
    parties=None,
):
    """Store a made-up vote held at noon UTC on the given day of January 2026.

    positions maps a bioguide ID to how that member voted, such as
    {"T000001": "Yea"}, and parties maps a bioguide ID to the member's party,
    which is "I" for anyone not named. The vote's category is worked out from
    its question, and its bill from its issue, exactly as the loader does it.
    """
    vote = {
        "chamber": chamber,
        "congress": 119,
        "session": session,
        "roll_number": roll_number,
        "voted_at": datetime(2026, 1, day, 12, 0, tzinfo=UTC),
        "question": question,
        "vote_type": None,
        "issue": issue,
        "title": title,
        "result": "Passed",
        "source_url": f"https://example.test/{chamber}/{roll_number}",
    }
    rows = [
        {
            "bioguide_id": b,
            "position": p,
            "party": (parties or {}).get(b, "I"),
            "state": "IL",
        }
        for b, p in positions.items()
    ]
    save(conn, vote, rows)


def summary(version_code, action_on, action_desc, text) -> dict:
    """Build a made-up bill_summaries row, minus its bill_id."""
    return {
        "version_code": version_code,
        "action_on": action_on,
        "action_desc": action_desc,
        "summary_text": text,
        "summary_html_source": f"<p>{text}</p>",
        "source_updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def add_bill(conn, bill_number=9999, summaries=(), policy_area="Energy") -> None:
    """Store a made-up House bill of the 119th Congress, sponsored by Pat Example.

    The default number is the bill the made-up votes name ('H R 9999').
    """
    bill = {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": bill_number,
        "title": "Example Act",
        "policy_area": policy_area,
        "sponsor_bioguide_id": "T000001",
        "introduced_on": date(2025, 1, 15),
        "latest_action_on": date(2025, 5, 9),
        "latest_action": "Became Public Law.",
        "source_url": f"https://example.test/bill/{bill_number}",
        "source_updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    save_bill(conn, bill, list(summaries))


# Generated summaries are written in order, a minute apart, from this moment.
FIRST_WRITTEN = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def add_generated(
    conn,
    text,
    status="approved",
    *,
    bill_number=9999,
    version_code="00",
    prompt_version="prompt-1",
    revised_from=None,
    minute=0,
) -> int:
    """Store a made-up generated summary of a stored bill and return its id.

    It is written `minute` minutes after FIRST_WRITTEN, so a test can say
    which came first. Anything but a draft is reviewed at the same moment.
    """
    written = FIRST_WRITTEN + timedelta(minutes=minute)
    row = conn.execute(
        """
        INSERT INTO generated_summaries (
            bill_id, source, source_version_code, plain_summary, impact, model,
            prompt_version, status, reviewed_at, input_tokens, output_tokens,
            cost_usd, generated_at, revised_from
        )
        SELECT id, 'crs_summary', %(version_code)s, %(text)s, %(impact)s,
               'claude-test', %(prompt_version)s, %(status)s, %(reviewed_at)s,
               100, 50, 0.001, %(written)s, %(revised_from)s
        FROM bills
        WHERE bill_number = %(bill_number)s
        RETURNING id
        """,
        {
            "version_code": version_code,
            "text": text,
            "impact": f"How {text} does you.",
            "prompt_version": prompt_version,
            "status": status,
            "reviewed_at": None if status == "draft" else written,
            "written": written,
            "revised_from": revised_from,
            "bill_number": bill_number,
        },
    )
    return row.fetchone()[0]


# The smallest thing the loader accepts as a JPEG: its three opening bytes.
JPEG = b"\xff\xd8\xff" + b"made-up photo"


def add_photo(conn, bioguide_id, image=JPEG, attribution=None) -> None:
    """Store a made-up photo. The member must already be in the members table."""
    source = "congress.gov" if attribution else "unitedstates"
    url = f"https://example.test/{bioguide_id}.jpg"
    conn.execute(
        UPSERT_PHOTO_SQL, to_photo_row(bioguide_id, image, source, url, attribution)
    )


def last_run(conn) -> dict:
    """Return the newest ingestion_runs row as a dict."""
    columns = "job, source, status, rows_loaded, error, finished_at"
    row = conn.execute(
        f"SELECT {columns} FROM ingestion_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(zip(columns.split(", "), row, strict=True))
