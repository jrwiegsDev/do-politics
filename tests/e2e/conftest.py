"""Setup for the browser tests: the real app, a real browser, made-up data.

These are end-to-end tests. Playwright drives a real Chromium through the
page the way a visitor does: it types into the fields, clicks the buttons and
reads what appears. The app runs in this same process, in a background
thread, against the throwaway test database, so a test can replace the
Census geocoder exactly as the other tests do. The geocoder is called by our
server, not by the browser, so the browser cannot be the one to fake it.

Every test in this folder is marked e2e, and a plain `pytest` skips them.
Run them with:
    uv run --env-file .env pytest -m "e2e and not census"
"""

import socket
import threading
import time
from datetime import date

import pytest
import uvicorn
from helpers import (
    REP_13,
    SENATOR_A,
    SENATOR_B,
    add,
    add_bill,
    add_members,
    add_photo,
    add_vote,
    summary,
)

from app.main import app
from ingest.members import UPSERT_SQL as UPSERT_MEMBER_SQL

# The Illinois State Capitol, which is in Illinois's 13th district.
CAPITOL = (-89.6548, 39.7984)


def pytest_collection_modifyitems(items):
    """Mark every test in this folder e2e, so the folder decides what kind it is."""
    for item in items:
        if "/e2e/" in item.path.as_posix():
            item.add_marker(pytest.mark.e2e)


def free_port() -> int:
    """Ask the operating system for a port nothing is using."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(database):
    """Run the app for the whole test session and return its address.

    The database fixture has already pointed every new connection at the
    test database, so the server's requests read the made-up data.
    """
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the test server did not start within 10 seconds")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def base_url(live_server):
    """Tell Playwright where the app is, so a test can write page.goto("/")."""
    return live_server


@pytest.fixture
def census_finds_the_capitol(monkeypatch):
    """Make every address resolve to the Capitol, so no test calls the Census."""
    monkeypatch.setattr("app.pages.geocode", lambda address: CAPITOL)


@pytest.fixture
def census_finds_nothing(monkeypatch):
    """Make every address come back unmatched, as a mistyped one would."""
    monkeypatch.setattr("app.pages.geocode", lambda address: None)


@pytest.fixture
def illinois_officials(committing_conn):
    """Seat two made-up senators and a representative for Illinois's 13th.

    The representative has a photo and one vote on a bill with an official
    summary; the senators have neither. The data is committed, because the
    server reads it over its own connection, and committing_conn removes it
    after the test.
    """
    add_members(committing_conn)
    for row in (SENATOR_A, SENATOR_B, REP_13):
        member = {
            "bioguide_id": row["bioguide_id"],
            "lis_id": None,
            "name": row["name"],
            "source": "legislators-current",
        }
        committing_conn.execute(UPSERT_MEMBER_SQL, member)
        add(committing_conn, row | {"term_end": date(2099, 1, 3)})

    add_photo(committing_conn, REP_13["bioguide_id"])
    add_bill(
        committing_conn,
        summaries=[
            summary("00", date(2025, 1, 15), "Introduced in House", "Does a thing.")
        ],
    )
    add_vote(committing_conn, 1, day=5, positions={REP_13["bioguide_id"]: "Yea"})
    committing_conn.commit()
