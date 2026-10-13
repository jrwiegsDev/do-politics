"""The DoPolitics API."""

from collections.abc import Iterator
from datetime import date
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.bills import find_bill
from app.geocoder import GeocoderUnavailable, geocode
from app.lookup import lookup
from app.members import find_member
from app.votes import ALL_CATEGORIES, FINAL_CATEGORIES, find_recent_votes
from ingest.db import connect

app = FastAPI(title="DoPolitics")


class LookupRequest(BaseModel):
    """The body of a lookup request. The address is used once and never stored."""

    address: str = Field(min_length=5, max_length=200)


class SummaryOut(BaseModel):
    """One official summary of a bill, as the API sends it."""

    version_code: str
    action_on: date
    action_desc: str
    summary_text: str


class BillOut(BaseModel):
    """A bill and its official summaries, as the API sends them.

    Only the fields named here and in SummaryOut are ever sent, whatever the
    query returned. That is what keeps a summary's original HTML off the wire.
    """

    congress: int
    bill_type: str
    bill_number: int
    title: str
    policy_area: str | None
    sponsor_bioguide_id: str
    sponsor_name: str
    introduced_on: date
    latest_action_on: date | None
    latest_action: str | None
    source_url: str
    summaries: list[SummaryOut]


BillType = Literal["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]


def get_conn() -> Iterator[psycopg.Connection]:
    """Open a database connection for one request and close it afterwards."""
    with connect() as conn:
        yield conn


# An endpoint asks for a database connection by giving a parameter this type.
Conn = Annotated[psycopg.Connection, Depends(get_conn)]


@app.get("/health")
def health(conn: Conn) -> dict:
    """Report that the API is up and can reach the database."""
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/lookup")
def lookup_address(request: LookupRequest, conn: Conn) -> dict:
    """Return the district and the members of Congress for a street address."""
    try:
        point = geocode(request.address)
    except GeocoderUnavailable:
        raise HTTPException(
            status_code=503,
            detail="The address service is unavailable. Try again shortly.",
        )

    if point is None:
        raise HTTPException(status_code=404, detail="That address could not be found.")

    longitude, latitude = point
    result = lookup(conn, longitude, latitude)
    if result is None:
        raise HTTPException(
            status_code=404, detail="That address is not in a congressional district."
        )

    return result


@app.get("/members/{bioguide_id}/votes")
def member_votes(
    conn: Conn,
    bioguide_id: Annotated[str, Path(pattern=r"^[A-Z]\d{6}$")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    show: Literal["final", "all"] = "final",
) -> dict:
    """Return a member's most recent votes, newest first.

    A Bioguide ID is a public identifier, so it can be part of the address.
    By default only final votes on legislation and nominations are returned;
    show=all adds procedural votes.
    """
    member = find_member(conn, bioguide_id)
    if member is None:
        raise HTTPException(
            status_code=404, detail="No member of Congress has that ID."
        )

    categories = ALL_CATEGORIES if show == "all" else FINAL_CATEGORIES
    return {
        "member": member,
        "votes": find_recent_votes(conn, bioguide_id, limit, categories),
    }


@app.get("/bills/{congress}/{bill_type}/{bill_number}", response_model=BillOut)
def bill(
    conn: Conn,
    congress: Annotated[int, Path(ge=1)],
    bill_type: BillType,
    bill_number: Annotated[int, Path(ge=1)],
) -> dict:
    """Return a bill and its official summaries, newest stage first.

    Each summary describes the bill at the stage it names, such as
    'Passed Senate'. A bill with no official summary has an empty list.
    """
    found = find_bill(conn, congress, bill_type, bill_number)
    if found is None:
        raise HTTPException(
            status_code=404, detail="No bill with that number is stored."
        )

    return found
