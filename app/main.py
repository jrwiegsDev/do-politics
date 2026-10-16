"""The DoPolitics API."""

from datetime import date
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Path, Query, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.address import Address
from app.bills import BillType, find_bill

# get_conn is imported so that `from app.main import get_conn` keeps working.
from app.deps import Conn, get_conn  # noqa: F401
from app.geocoder import GeocoderUnavailable, geocode
from app.lookup import lookup
from app.members import find_member
from app.pages import router as pages_router
from app.photos import etag_for, etag_matches, find_photo
from app.votes import ALL_CATEGORIES, FINAL_CATEGORIES, find_recent_votes

app = FastAPI(title="DoPolitics")

# The stylesheet and the HTMX script are served from our own folder, so a
# visitor's browser never has to ask another site for anything.
app.mount(
    "/static",
    StaticFiles(directory=FilePath(__file__).parent / "static"),
    name="static",
)
app.include_router(pages_router)


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


@app.get("/health")
def health(conn: Conn) -> dict:
    """Report that the API is up and can reach the database."""
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/lookup")
def lookup_address(address: Address, conn: Conn) -> dict:
    """Return the district and the members of Congress for a street address.

    The address is the request body. It is used once and never stored.
    """
    try:
        point = geocode(address)
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


# How long a browser may reuse a photo before asking again: a day. Asking
# again costs little, because an unchanged photo is answered with 304.
PHOTO_MAX_AGE = 86400


@app.get("/photos/{bioguide_id}.jpg", response_class=Response)
def photo(
    conn: Conn,
    bioguide_id: Annotated[str, Path(pattern=r"^[A-Z]\d{6}$")],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Return a member's photo, or 304 Not Modified when the browser already has it.

    The ETag is the photo's SHA-256, so it changes exactly when the image does.
    """
    found = find_photo(conn, bioguide_id)
    if found is None:
        raise HTTPException(
            status_code=404, detail="No photo is stored for that member."
        )

    image, sha256 = found
    headers = {
        "ETag": etag_for(sha256),
        "Cache-Control": f"public, max-age={PHOTO_MAX_AGE}",
    }
    if etag_matches(if_none_match, sha256):
        return Response(status_code=304, headers=headers)
    return Response(image, media_type="image/jpeg", headers=headers)


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
