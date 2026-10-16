"""Browser tests: what happens to an address, found or not."""

import pytest
from playwright.sync_api import Page, Request, expect

STREET = "401 S 2nd St"
CITY = "Springfield"


def fill_in_the_capitol(page: Page) -> None:
    """Type the Illinois State Capitol's address into the form, without sending it."""
    page.get_by_label("Street address").fill(STREET)
    page.get_by_label("City").fill(CITY)
    page.get_by_label("State").fill("IL")
    page.get_by_label("ZIP code").fill("62701")


def test_an_address_the_census_cannot_find_gets_a_message(
    page: Page, census_finds_nothing
):
    page.goto("/")
    fill_in_the_capitol(page)

    page.get_by_role("button", name="Find my officials").click()

    expect(page.get_by_role("status")).to_have_text(
        "Sorry! We couldn't locate that address. Try typing it in again."
    )


def test_the_address_never_appears_in_a_url(
    page: Page, base_url, illinois_officials, census_finds_the_capitol
):
    # Note every URL the browser asks for, from the page itself to each photo.
    urls: list[str] = []

    def note(request: Request) -> None:
        urls.append(request.url)

    page.on("request", note)
    page.goto("/")
    fill_in_the_capitol(page)

    page.get_by_role("button", name="Find my officials").click()
    expect(page.get_by_role("button", name="Casey Rep")).to_be_visible()

    # The address went in the request's body, which is not part of its URL.
    assert page.url == f"{base_url}/"
    assert urls, "the browser made no requests"
    for url in urls:
        assert "2nd" not in url
        assert CITY not in url


@pytest.mark.census
def test_the_real_census_places_the_capitol_in_the_13th_district(
    page: Page, illinois_officials
):
    """Call the real Census Geocoder. It needs the internet, so it runs only when asked:

    uv run --env-file .env pytest -m census
    """
    page.goto("/")
    fill_in_the_capitol(page)

    page.get_by_role("button", name="Find my officials").click()

    # The real service can take a few seconds, longer than expect's usual 5.
    expect(page.locator("#officials-tile")).to_contain_text(
        "Congressional District 13", timeout=15_000
    )
