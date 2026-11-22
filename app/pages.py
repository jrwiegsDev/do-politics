"""The pages people see. Each route returns HTML built from a template."""

from datetime import date, datetime
from pathlib import Path as FilePath
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Path, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.address import Address
from app.bills import (
    BillType,
    find_bill,
    find_bill_topics,
    find_member_votes,
    search_bills,
)
from app.deps import Conn, lookup_wait
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
from app.search import MAX_LENGTH, read_search
from app.usage import count_bill_view, count_lookup, count_search
from app.votes import find_attendance, find_policy_areas, find_recent_votes
from ingest.congress import bill_label, ordinal, today_in_washington

# How many votes the member tile shows at a time.
PAGE_SIZE = 20

# Where visitors send questions about the privacy policy and terms of use. It
# forwards to the maintainer's inbox. If it is ever emptied, the pages leave
# out their Questions section rather than show a blank address.
CONTACT_EMAIL = "contact@dopolitics.org"

# Congress works on Eastern time, so that is the day a vote is said to be on.
EASTERN = ZoneInfo("America/New_York")

BioguideId = Annotated[str, Path(pattern=r"^[A-Z]\d{6}$")]

router = APIRouter(default_response_class=HTMLResponse, include_in_schema=False)
templates = Jinja2Templates(directory=FilePath(__file__).parent / "templates")

# The site's icon: a capitol in the page's colors, drawn as an SVG so one
# small file stays sharp at every size.
FAVICON = FilePath(__file__).parent / "static" / "favicon.svg"

# The same icon as a solid 180x180 PNG, which iPhones and iPads use for the
# Home Screen and for tabs. Drawn from the SVG by scripts/favicon_pngs.py.
TOUCH_ICON = FilePath(__file__).parent / "static" / "apple-touch-icon.png"


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
# | long_date }}, {{ bill.bill_type | bill_label(bill.bill_number) }},
# {{ row.paid | dollars }} and {{ bill.congress | ordinal }}.
templates.env.filters["long_date"] = long_date
templates.env.filters["vote_date"] = vote_date
templates.env.filters["bill_label"] = bill_label
templates.env.filters["dollars"] = dollars
templates.env.filters["ordinal"] = ordinal


def vote_page(
    conn: Conn, bioguide_id: str, topic: str | None, offset: int, search: str = ""
) -> dict:
    """Return one page of a member's votes and where the next page starts.

    One more vote than a page holds is asked for. If it comes back there is
    a next page, and the extra vote is left for it. The search, as typed, is
    read into a bill number or words; see read_search.
    """
    votes = find_recent_votes(
        conn,
        bioguide_id,
        PAGE_SIZE + 1,
        policy_area=topic,
        offset=offset,
        **read_search(search),
    )
    return {
        "bioguide_id": bioguide_id,
        "topic": topic,
        "search": search,
        "votes": votes[:PAGE_SIZE],
        "next_offset": offset + PAGE_SIZE if len(votes) > PAGE_SIZE else None,
    }


def bill_search_page(conn: Conn, search: str, topic: str | None, offset: int) -> dict:
    """Return one page of the bills a search finds and where the next page starts.

    Like vote_page, one more bill than a page holds is asked for, to know
    whether there is a next page.
    """
    bills = search_bills(
        conn, PAGE_SIZE + 1, offset=offset, policy_area=topic, **read_search(search)
    )
    return {
        "search": search,
        "topic": topic,
        "bills": bills[:PAGE_SIZE],
        "next_offset": offset + PAGE_SIZE if len(bills) > PAGE_SIZE else None,
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


# HEAD as well as GET: Render probes the front page with HEAD when a deploy starts.
@router.head("/")
@router.get("/")
def index(request: Request, conn: Conn) -> HTMLResponse:
    """Return the whole page: the greeting, the address tile and Bill Search.

    Bill Search's topic dropdown lists the topics of the stored bills.
    """
    context = {"topics": find_bill_topics(conn)}
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/favicon.ico")
def favicon() -> FileResponse:
    """Return the site's icon at the path browsers and crawlers ask for by habit.

    Pages point to /static/favicon.svg, but many clients request /favicon.ico
    without reading the page first. Sending the same SVG here, with its real
    type, answers them instead of logging a 404 on every visit.
    """
    return FileResponse(FAVICON, media_type="image/svg+xml")


@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
def touch_icon() -> FileResponse:
    """Return the Home Screen icon at the paths iPhones and iPads ask for by habit.

    Pages point to /static/apple-touch-icon.png, but iOS also requests these
    two fixed paths, the second an older name for the same icon.
    """
    return FileResponse(TOUCH_ICON, media_type="image/png")


@router.get("/privacy")
def privacy(request: Request) -> HTMLResponse:
    """Return the privacy policy: what happens to information about a visitor."""
    return templates.TemplateResponse(
        request, "privacy.html", {"contact_email": CONTACT_EMAIL}
    )


@router.get("/terms")
def terms(request: Request) -> HTMLResponse:
    """Return the terms of use."""
    return templates.TemplateResponse(
        request, "terms.html", {"contact_email": CONTACT_EMAIL}
    )


@router.get("/tiles/clear")
def clear_tiles(request: Request) -> HTMLResponse:
    """Return every tile below the address emptied, for the Clear button."""
    return templates.TemplateResponse(request, "_clear_all.html")


@router.get("/tiles/bills/clear")
def clear_bill_search(request: Request) -> HTMLResponse:
    """Return Bill Search's results and the bill tile emptied, for its Clear button."""
    return templates.TemplateResponse(request, "_clear_search.html")


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
        count_lookup(conn, "invalid")
        return message(
            request,
            "Enter a street address with a ZIP code, or with a city and state.",
            422,
            clear_tiles=True,
        )

    # Counted after the address is checked, since only a lookup that reaches
    # the Census needs limiting.
    wait = lookup_wait(request)
    if wait is not None:
        count_lookup(conn, "rate_limited")
        response = message(
            request,
            "That's a lot of lookups in a short time. Try again in a minute.",
            429,
            clear_tiles=True,
        )
        response.headers["Retry-After"] = str(wait)
        return response

    try:
        point = geocode(address)
    except GeocoderUnavailable:
        count_lookup(conn, "unavailable")
        return message(
            request,
            "The address service is unavailable. Try again shortly.",
            503,
            clear_tiles=True,
        )

    if point is None:
        count_lookup(conn, "not_found")
        return message(
            request,
            "Sorry! We couldn't locate that address. Try typing it in again.",
            404,
            clear_tiles=True,
        )

    longitude, latitude = point
    result = lookup(conn, longitude, latitude)
    if result is None:
        count_lookup(conn, "no_district")
        return message(
            request,
            "That address is not in a congressional district.",
            404,
            clear_tiles=True,
        )

    # Only the state is counted; the district, like the address, is not kept.
    count_lookup(conn, "found", result["district"]["state_fips"])
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
    today = today_in_washington()
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
    q: Annotated[str, Query(max_length=MAX_LENGTH)] = "",
) -> HTMLResponse:
    """Return a page of a member's votes as list rows, for a topic and search if given.

    The topic, the search and the offset arrive with every request, so the
    server keeps nothing between one request and the next.
    """
    # The dropdown's "All topics" choice sends an empty topic.
    context = vote_page(conn, bioguide_id, topic or None, offset, q)
    return templates.TemplateResponse(request, "_vote_rows.html", context)


@router.get("/tiles/bills/search")
def bill_rows(
    request: Request,
    conn: Conn,
    q: Annotated[str, Query(max_length=MAX_LENGTH)] = "",
    topic: Annotated[str, Query(max_length=100)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    """Return a page of the bills a search and topic find, as list rows.

    A topic alone lists that topic's bills. With neither, nothing at all is
    returned, so clearing the box empties the list, and the list's :empty
    rule hides it. The dropdown's "All topics" choice sends an empty topic.
    """
    if not q.strip() and not topic:
        return HTMLResponse("")
    context = bill_search_page(conn, q, topic or None, offset)
    # Results come as you type, so this counts requests, not searches. A
    # "More" page continues one already counted.
    if offset == 0:
        count_search(conn, found_nothing=not context["bills"])
    return templates.TemplateResponse(request, "_bill_rows.html", context)


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

    # A member's vote rows link here with ?member=; Bill Search links without it.
    opened_from = "votes" if member else "search"
    count_bill_view(conn, congress, bill_type, bill_number, opened_from)

    official = find_official(conn, member) if member else None
    member_votes = (
        find_member_votes(conn, congress, bill_type, bill_number, member)
        if official and member
        else []
    )
    context = {
        "bill": bill,
        "official": official,
        "member_votes": member_votes,
        "opened_from": opened_from,
    }
    return templates.TemplateResponse(request, "_bill.html", context)
