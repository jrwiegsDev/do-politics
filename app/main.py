"""The DoPolitics API."""

from collections.abc import Iterator
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.geocoder import GeocoderUnavailable, geocode
from app.lookup import lookup
from app.members import find_member
from app.votes import ALL_CATEGORIES, FINAL_CATEGORIES, find_recent_votes
from ingest.db import connect

app = FastAPI(title="DoPolitics")


class LookupRequest(BaseModel):
    """The body of a lookup request. The address is used once and never stored."""

    address: str = Field(min_length=5, max_length=200)


def get_conn() -> Iterator[psycopg.Connection]:
    """Open a database connection for one request and close it afterwards."""
    with connect() as conn:
        yield conn


@app.get("/health")
def health(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Report that the API is up and can reach the database."""
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/lookup")
def lookup_address(request: LookupRequest, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Return the district and the members of Congress for a street address."""
    try:
        point = geocode(request.address)
    except GeocoderUnavailable:
        raise HTTPException(status_code=503, detail="The address service is unavailable. Try again shortly.")

    if point is None:
        raise HTTPException(status_code=404, detail="That address could not be found.")

    longitude, latitude = point
    result = lookup(conn, longitude, latitude)
    if result is None:
        raise HTTPException(status_code=404, detail="That address is not in a congressional district.")

    return result


@app.get("/members/{bioguide_id}/votes")
def member_votes(
    bioguide_id: str = Path(pattern=r"^[A-Z]\d{6}$"),
    limit: int = Query(default=20, ge=1, le=100),
    show: Literal["final", "all"] = "final",
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """Return a member's most recent votes, newest first.

    A Bioguide ID is a public identifier, so it can be part of the address.
    By default only final votes on legislation and nominations are returned;
    show=all adds procedural votes.
    """
    member = find_member(conn, bioguide_id)
    if member is None:
        raise HTTPException(status_code=404, detail="No member of Congress has that ID.")

    categories = ALL_CATEGORIES if show == "all" else FINAL_CATEGORIES
    return {"member": member, "votes": find_recent_votes(conn, bioguide_id, limit, categories)}
