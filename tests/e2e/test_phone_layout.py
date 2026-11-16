"""Browser tests: the page on a phone-sized screen, and the wide layout kept.

Below the 50rem breakpoint (800px) the tiles stack, each as tall as what it
holds, and the page itself scrolls. Above it the officials and member tiles
share a row, each scrolls inside itself, and the member's details stay
pinned. These tests check both sides of the breakpoint.
"""

import pytest
from playwright.sync_api import Page, ViewportSize, expect

pytestmark = pytest.mark.usefixtures("illinois_officials", "census_finds_the_capitol")

# The iPhone SE's screen in CSS pixels, the narrowest phone in common use.
PHONE: ViewportSize = {"width": 375, "height": 667}
WIDE: ViewportSize = {"width": 1280, "height": 800}

# Longer than any real topic, standing in for the longest one in the list.
LONG_TOPIC = "Armed Forces and National Security, Science, Technology, Communications"


def open_a_member(page: Page, size: ViewportSize) -> None:
    """Look up the Capitol at the given screen size and open the representative."""
    page.set_viewport_size(size)
    page.goto("/")
    page.get_by_label("Street address").fill("401 S 2nd St")
    page.get_by_label("City").fill("Springfield")
    page.get_by_label("State").fill("IL")
    page.get_by_label("ZIP code").fill("62701")
    page.get_by_role("button", name="Find my officials").click()
    page.get_by_role("button", name="Casey Rep").click()
    expect(
        page.locator("#member-tile").get_by_role("heading", name="Votes")
    ).to_be_visible()


def test_on_a_phone_the_page_never_scrolls_sideways(page: Page):
    open_a_member(page, PHONE)
    # A dropdown is as wide as its longest option unless the layout lets it
    # shrink; the longest real topic once pushed the tile past the screen.
    page.locator("#topic").evaluate(
        "(select, text) => select.add(new Option(text, ''))", LONG_TOPIC
    )

    widths = page.evaluate(
        """() => ({
            page: document.documentElement.scrollWidth,
            screen: window.innerWidth,
            tile: document.getElementById('member-tile').scrollWidth,
            tileBox: document.getElementById('member-tile').clientWidth,
        })"""
    )

    assert widths["page"] <= widths["screen"]
    assert widths["tile"] <= widths["tileBox"]


def test_on_a_phone_a_tile_is_only_as_tall_as_what_it_holds(page: Page):
    open_a_member(page, PHONE)

    # The tile's height against its content plus padding and border: no
    # fixed height leaving empty space under the officials.
    spare = page.locator("#officials-tile").evaluate(
        """tile => {
            const style = getComputedStyle(tile);
            const frame = ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']
                .reduce((sum, side) => sum + parseFloat(style[side]), 0);
            const content = [...tile.children]
                .reduce((sum, child) => sum + child.getBoundingClientRect().height, 0);
            return tile.getBoundingClientRect().height - frame - content;
        }"""
    )

    assert spare < 40  # margins between the heading and the list, not a gap


def test_on_a_phone_the_member_s_details_do_not_cover_the_votes(page: Page):
    open_a_member(page, PHONE)
    pinned = page.locator(".member-pinned")
    first_vote = page.locator("#member-tile .vote").first

    position = pinned.evaluate("el => getComputedStyle(el).position")
    # bounding_box() is None for an element that is not drawn on the page.
    pinned_box = pinned.bounding_box()
    vote_box = first_vote.bounding_box()
    assert pinned_box is not None
    assert vote_box is not None

    assert position != "sticky"
    assert vote_box["y"] >= pinned_box["y"] + pinned_box["height"]
    expect(first_vote).to_be_visible()


def add_rows(page: Page, list_selector: str, count: int = 60) -> None:
    """Fill a list with made-up rows, as many "Show more votes" clicks would."""
    page.locator(list_selector).evaluate(
        """(list, count) => {
            for (let i = 0; i < count; i++) {
                list.insertAdjacentHTML('beforeend', '<li class="vote">A vote</li>');
            }
        }""",
        count,
    )


@pytest.mark.parametrize("list_selector", ["#vote-list", ".officials"])
def test_on_a_phone_a_long_list_scrolls_inside_its_own_box(page: Page, list_selector):
    open_a_member(page, PHONE)
    page_height_before = page.evaluate("document.documentElement.scrollHeight")

    add_rows(page, list_selector)

    box = page.locator(list_selector).evaluate(
        """list => ({
            height: list.getBoundingClientRect().height,
            content: list.scrollHeight,
            screen: window.innerHeight,
        })"""
    )
    page_height_after = page.evaluate("document.documentElement.scrollHeight")

    assert box["height"] <= 0.7 * box["screen"] + 1
    assert box["content"] > box["height"]
    # The page grows by at most the box, however many rows it holds.
    assert page_height_after - page_height_before <= 0.7 * box["screen"] + 1


def test_on_a_wide_screen_the_votes_list_does_not_scroll_inside_the_tile(page: Page):
    # The whole member tile scrolls there; a second scroll box inside it
    # would trap the pointer.
    open_a_member(page, WIDE)
    add_rows(page, "#vote-list")

    overflow = page.locator("#vote-list").evaluate(
        "el => getComputedStyle(el).overflowY"
    )

    assert overflow == "visible"


def test_on_a_wide_screen_the_member_s_details_stay_pinned_in_a_scrolling_tile(
    page: Page,
):
    open_a_member(page, WIDE)

    styles = page.evaluate(
        """() => ({
            pinned: getComputedStyle(document.querySelector('.member-pinned')).position,
            overflow: getComputedStyle(document.getElementById('member-tile')).overflowY,
        })"""
    )

    assert styles == {"pinned": "sticky", "overflow": "auto"}
