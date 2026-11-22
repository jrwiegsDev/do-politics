"""Browser tests: the order of the tiles down the page.

The tiles an address opens come first and Bill Search after them, so a
lookup reads top to bottom. A bill appears next to where it was opened:
under the member from a vote, under Bill Search from a search. With no
address, Bill Search sits right under the address form. The order is set by
the stylesheet, so only a browser can check it.
"""

import pytest
from playwright.sync_api import Page, ViewportSize, expect

pytestmark = pytest.mark.usefixtures("illinois_officials", "census_finds_the_capitol")

PHONE: ViewportSize = {"width": 375, "height": 667}
WIDE: ViewportSize = {"width": 1280, "height": 800}
GAP = 16  # the grid's 1rem gap, in CSS pixels

# Every shown tile's id, top of the page first.
TILES_TOP_DOWN = """() =>
  [...document.querySelectorAll('.tiles > .tile')]
    .filter(t => t.offsetParent)
    .map(t => [t.id, t.getBoundingClientRect().top + scrollY])
    .sort((a, b) => a[1] - b[1])
    .map(t => t[0])"""

# The space between each pair of stacked tiles, in CSS pixels.
GAPS = """() => {
  const tiles = [...document.querySelectorAll('.tiles > .tile')]
    .filter(t => t.offsetParent)
    .map(t => t.getBoundingClientRect())
    .sort((a, b) => a.top - b.top);
  return tiles.slice(1)
    .map((t, i) => Math.round(t.top - tiles[i].bottom))
    .filter(gap => gap > 0);
}"""


def look_up_the_capitol(page: Page, size: ViewportSize) -> None:
    page.set_viewport_size(size)
    page.goto("/")
    page.get_by_label("Street address").fill("401 S 2nd St")
    page.get_by_label("ZIP code").fill("62701")
    page.get_by_role("button", name="Find my officials").click()
    expect(page.locator("#officials-tile")).to_contain_text("Casey Rep")


def open_from_search(page: Page) -> None:
    page.get_by_label("Find a bill").fill("example")
    results = page.locator("#bill-results")
    results.get_by_role("button", name="Example Act").click()
    expect(page.locator("#bill-tile")).to_contain_text("Does a thing.")


@pytest.mark.parametrize("size", [PHONE, WIDE], ids=["phone", "wide"])
def test_with_no_address_bill_search_and_its_bill_follow_the_form(
    page: Page, size: ViewportSize
):
    page.set_viewport_size(size)
    page.goto("/")
    assert page.evaluate(TILES_TOP_DOWN) == ["address-tile", "search-tile"]

    open_from_search(page)

    assert page.evaluate(TILES_TOP_DOWN) == [
        "address-tile",
        "search-tile",
        "bill-tile",
    ]
    assert page.evaluate(GAPS) == [GAP, GAP]


@pytest.mark.parametrize("size", [PHONE, WIDE], ids=["phone", "wide"])
def test_after_a_lookup_bill_search_comes_after_the_officials(
    page: Page, size: ViewportSize
):
    look_up_the_capitol(page, size)

    assert page.evaluate(TILES_TOP_DOWN) == [
        "address-tile",
        "officials-tile",
        "search-tile",
    ]
    # An empty member or bill tile leaves no extra space above the search.
    assert page.evaluate(GAPS) == [GAP, GAP]


@pytest.mark.parametrize("size", [PHONE, WIDE], ids=["phone", "wide"])
def test_a_bill_from_a_vote_sits_under_the_member_above_bill_search(
    page: Page, size: ViewportSize
):
    look_up_the_capitol(page, size)
    page.get_by_role("button", name="Casey Rep").click()

    page.locator("#member-tile").get_by_role("button", name="Example Act").click()
    expect(page.locator("#bill-tile")).to_contain_text("Does a thing.")

    order = page.evaluate(TILES_TOP_DOWN)
    assert order.index("member-tile") < order.index("bill-tile")
    assert order.index("bill-tile") < order.index("search-tile")


@pytest.mark.parametrize("size", [PHONE, WIDE], ids=["phone", "wide"])
def test_a_bill_from_a_search_sits_under_bill_search(page: Page, size: ViewportSize):
    look_up_the_capitol(page, size)
    page.get_by_role("button", name="Casey Rep").click()

    open_from_search(page)

    order = page.evaluate(TILES_TOP_DOWN)
    assert order[-2:] == ["search-tile", "bill-tile"]
    assert order.index("member-tile") < order.index("search-tile")


def member_tile_height(page: Page) -> int:
    return page.evaluate("() => document.querySelector('#member-tile').clientHeight")


def test_a_bill_from_a_search_leaves_the_member_tile_its_full_height(page: Page):
    look_up_the_capitol(page, WIDE)
    page.get_by_role("button", name="Casey Rep").click()
    expect(page.locator("#member-tile")).to_contain_text("Votes")
    full = member_tile_height(page)

    open_from_search(page)
    assert member_tile_height(page) == full

    # A bill from a vote sits right under the member, so the tiles above it
    # give it room.
    page.locator("#member-tile").get_by_role("button", name="Example Act").click()
    expect(page.locator("#bill-tile [data-opened-from='votes']")).to_be_attached()
    assert member_tile_height(page) < full
