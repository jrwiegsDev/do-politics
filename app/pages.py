"""The pages people see. Each route returns HTML built from a template."""

from datetime import date, datetime
from pathlib import Path as FilePath
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Path, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.address import Address
from app.bills import BillType, bill_label, find_bill, find_member_votes
from app.deps import Conn
from app.geocoder import GeocoderUnavailable, geocode
from app.lookup import lookup
from app.officials import find_official
from app.pay import (
    MINIMUM_WAGE_DAY,
    MINIMUM_WAGE_YEAR,
    PAY_SOURCE_URL,
    pay_for_session,
    salary,
)
from app.votes import find_attendance, find_policy_areas, find_recent_votes

# How many votes the member tile shows at a time.
PAGE_SIZE = 20

# Congress works on Eastern time, so that is the day a vote is said to be on.
EASTERN = ZoneInfo("America/New_York")

BioguideId = Annotated[str, Path(pattern=r"^[A-Z]\d{6}$")]

router = APIRouter(default_response_class=HTMLResponse, include_in_schema=False)
templates = Jinja2Templates(directory=FilePath(__file__).parent / "templates")


def long_date(day: date) -> str:
    """Return a day written out, such as 'Sep 16, 2026'."""
    return f"{day:%b} {day.day}, {day.year}"


def dollars(amount: int) -> str:
    """Return a whole-dollar amount written out, such as '$174,000'."""
    return f"${amount:,}"


def vote_date(moment: datetime) -> str:
    """Return the day a vote was held, in Washington's time, such as 'Sep 16, 2026'."""
    return long_date(moment.astimezone(EASTERN))


# A template uses these as {{ vote.voted_at | vote_date }}, {{ bill.introduced_on
# | long_date }}, {{ bill.bill_type | bill_label(bill.bill_number) }} and
# {{ row.paid | dollars }}.
templates.env.filters["long_date"] = long_date
templates.env.filters["vote_date"] = vote_date
templates.env.filters["bill_label"] = bill_label
templates.env.filters["dollars"] = dollars


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


def message(
    request: Request, text: str, status_code: int, clear_tiles: bool = False
) -> HTMLResponse:
    """Return a one-line message to show in place of a tile.

    With clear_tiles, the member and bill tiles are emptied as well. The
    officials tile asks for that, because a new address makes them stale.
    """
    context = {"text": text, "clear_tiles": clear_tiles}
    return templates.TemplateResponse(
        request, "_message.html", context, status_code=status_code
    )


@router.get("/")
def index(request: Request) -> HTMLResponse:
    """Return the whole page: the greeting and the address tile."""
    return templates.TemplateResponse(request, "index.html")


@router.get("/tiles/clear")
def clear_tiles(request: Request) -> HTMLResponse:
    """Return every tile below the address emptied, for the Clear button."""
    return templates.TemplateResponse(request, "_clear_all.html")


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
            clear_tiles=True,
        )

    try:
        point = geocode(address)
    except GeocoderUnavailable:
        return message(
            request,
            "The address service is unavailable. Try again shortly.",
            503,
            clear_tiles=True,
        )

    if point is None:
        return message(
            request,
            "Sorry! We couldn't locate that address. Try typing it in again.",
            404,
            clear_tiles=True,
        )

    longitude, latitude = point
    result = lookup(conn, longitude, latitude)
    if result is None:
        return message(
            request,
            "That address is not in a congressional district.",
            404,
            clear_tiles=True,
        )

    return templates.TemplateResponse(request, "_officials.html", result)


@router.get("/tiles/members/{bioguide_id}")
def member_tile(request: Request, conn: Conn, bioguide_id: BioguideId) -> HTMLResponse:
    """Return the member tile: contact details, attendance and recent votes.

    A Bioguide ID is a public identifier, so it can be part of the address.
    """
    official = find_official(conn, bioguide_id)
    if official is None:
        return message(request, "Nobody holding a seat in Congress has that ID.", 404)

    # The member's current leadership roles set the pay for every year shown.
    yearly = salary(official["leadership_titles"])
    today = date.today()
    attendance = [
        pay_for_session(row, yearly, official["term_start"], today)
        for row in find_attendance(conn, bioguide_id)
    ]
    context = vote_page(conn, bioguide_id, topic=None, offset=0) | {
        "official": official,
        "attendance": attendance,
        "salary": yearly,
        "topics": find_policy_areas(conn, bioguide_id),
        "minimum_wage_day": MINIMUM_WAGE_DAY,
        "minimum_wage_year": MINIMUM_WAGE_YEAR,
        "pay_source_url": PAY_SOURCE_URL,
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


@router.get("/tiles/bills/{congress}/{bill_type}/{bill_number}")
def bill_tile(
    request: Request,
    conn: Conn,
    congress: Annotated[int, Path(ge=1)],
    bill_type: BillType,
    bill_number: Annotated[int, Path(ge=1)],
    member: Annotated[str | None, Query(pattern=r"^[A-Z]\d{6}$")] = None,
) -> HTMLResponse:
    """Return the bill tile: the bill, where it stands, and its official summaries.

    Opened from a member's votes, it names that member, so the tile also
    says how they voted on the bill.
    """
    bill = find_bill(conn, congress, bill_type, bill_number)
    if bill is None:
        return message(request, "That bill is not in our records yet.", 404)

    official = find_official(conn, member) if member else None
    member_votes = (
        find_member_votes(conn, congress, bill_type, bill_number, member)
        if official and member
        else []
    )
    context = {"bill": bill, "official": official, "member_votes": member_votes}
    return templates.TemplateResponse(request, "_bill.html", context)
