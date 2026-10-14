"""The pages people see. Each route returns HTML built from a template."""

from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Path, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.address import Address
from app.deps import Conn
from app.geocoder import GeocoderUnavailable, geocode
from app.lookup import lookup
from app.officials import find_official
from app.votes import find_attendance, find_policy_areas, find_recent_votes

# How many votes the member tile shows at a time.
PAGE_SIZE = 20

# Congress works on Eastern time, so that is the day a vote is said to be on.
EASTERN = ZoneInfo("America/New_York")

BioguideId = Annotated[str, Path(pattern=r"^[A-Z]\d{6}$")]

router = APIRouter(default_response_class=HTMLResponse, include_in_schema=False)
templates = Jinja2Templates(directory=FilePath(__file__).parent / "templates")


def vote_date(moment: datetime) -> str:
    """Return the day a vote was held, in Washington's time, such as 'Sep 16, 2026'."""
    day = moment.astimezone(EASTERN)
    return f"{day:%b} {day.day}, {day.year}"


# A template uses this as {{ vote.voted_at | vote_date }}.
templates.env.filters["vote_date"] = vote_date


def vote_page(conn: Conn, bioguide_id: str, topic: str | None, offset: int) -> dict:
    """Return one page of a member's votes and where the next page starts.

    One more vote than a page holds is asked for. If it comes back there is
    a next page, and the extra vote is left for it.
    """
    votes = find_recent_votes(
        conn, bioguide_id, PAGE_SIZE + 1, policy_area=topic, offset=offset
    )
    return {
        "bioguide_id": bioguide_id,
        "topic": topic,
        "votes": votes[:PAGE_SIZE],
        "next_offset": offset + PAGE_SIZE if len(votes) > PAGE_SIZE else None,
    }


def message(request: Request, text: str, status_code: int) -> HTMLResponse:
    """Return a one-line message to show in place of a tile."""
    return templates.TemplateResponse(
        request, "_message.html", {"text": text}, status_code=status_code
    )


@router.get("/")
def index(request: Request) -> HTMLResponse:
    """Return the whole page: the greeting and the address tile."""
    return templates.TemplateResponse(request, "index.html")


@router.post("/tiles/officials")
def officials_tile(
    request: Request,
    conn: Conn,
    street: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    state: Annotated[str, Form()] = "",
    zip_code: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Return the officials tile for an address, or a message saying why not."""
    try:
        address = Address(street=street, city=city, state=state, zip_code=zip_code)
    except ValidationError:
        # The error itself is not used: its text quotes what was typed.
        return message(
            request,
            "Enter a street address with a ZIP code, or with a city and state.",
            422,
        )

    try:
        point = geocode(address)
    except GeocoderUnavailable:
        return message(
            request, "The address service is unavailable. Try again shortly.", 503
        )

    if point is None:
        return message(
            request,
            "Sorry! We couldn't locate that address. Try typing it in again.",
            404,
        )

    longitude, latitude = point
    result = lookup(conn, longitude, latitude)
    if result is None:
        return message(request, "That address is not in a congressional district.", 404)

    return templates.TemplateResponse(request, "_officials.html", result)


@router.get("/tiles/members/{bioguide_id}")
def member_tile(request: Request, conn: Conn, bioguide_id: BioguideId) -> HTMLResponse:
    """Return the member tile: contact details, attendance and recent votes.

    A Bioguide ID is a public identifier, so it can be part of the address.
    """
    official = find_official(conn, bioguide_id)
    if official is None:
        return message(request, "Nobody holding a seat in Congress has that ID.", 404)

    context = vote_page(conn, bioguide_id, topic=None, offset=0) | {
        "official": official,
        "attendance": find_attendance(conn, bioguide_id),
        "topics": find_policy_areas(conn, bioguide_id),
    }
    return templates.TemplateResponse(request, "_member.html", context)


@router.get("/tiles/members/{bioguide_id}/votes")
def vote_rows(
    request: Request,
    conn: Conn,
    bioguide_id: BioguideId,
    topic: Annotated[str, Query(max_length=100)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    """Return a page of a member's votes as list rows, for a topic if one is named.

    The topic and the offset arrive with every request, so the server keeps
    nothing between one request and the next.
    """
    # The dropdown's "All topics" choice sends an empty topic.
    context = vote_page(conn, bioguide_id, topic or None, offset)
    return templates.TemplateResponse(request, "_vote_rows.html", context)
