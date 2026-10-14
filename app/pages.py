"""The pages people see. Each route returns HTML built from a template."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.deps import Conn
from app.geocoder import GeocoderUnavailable, geocode
from app.lookup import lookup

router = APIRouter(default_response_class=HTMLResponse, include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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
    address: Annotated[str, Form(min_length=5, max_length=200)],
) -> HTMLResponse:
    """Return the officials tile for an address, or a message saying why not."""
    try:
        point = geocode(address)
    except GeocoderUnavailable:
        return message(
            request, "The address service is unavailable. Try again shortly.", 503
        )

    if point is None:
        return message(request, "That address could not be found.", 404)

    longitude, latitude = point
    result = lookup(conn, longitude, latitude)
    if result is None:
        return message(request, "That address is not in a congressional district.", 404)

    return templates.TemplateResponse(request, "_officials.html", result)
