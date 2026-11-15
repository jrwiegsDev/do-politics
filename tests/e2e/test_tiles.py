"""Browser tests: from the officials tile to a member, a bill, and back to empty."""

import pytest
from playwright.sync_api import Page, expect

# Every test here needs the made-up officials and the faked Census. A test
# can still name a fixture it uses directly, such as page.
pytestmark = pytest.mark.usefixtures("illinois_officials", "census_finds_the_capitol")


def look_up_the_capitol(page: Page) -> None:
    """Open the front page and look up the Illinois State Capitol's address."""
    page.goto("/")
    page.get_by_label("Street address").fill("401 S 2nd St")
    page.get_by_label("City").fill("Springfield")
    page.get_by_label("State").fill("IL")
    page.get_by_label("ZIP code").fill("62701")
    page.get_by_role("button", name="Find my officials").click()


def test_a_stored_photo_reaches_the_browser(page: Page):
    # Wait for the browser's own request for the photo, and keep its answer.
    with page.expect_response("**/photos/T000012.jpg") as photo:
        look_up_the_capitol(page)

    assert photo.value.status == 200
    assert photo.value.headers["content-type"] == "image/jpeg"


def test_choosing_a_name_opens_that_members_votes(page: Page):
    look_up_the_capitol(page)

    page.get_by_role("button", name="Casey Rep").click()

    member = page.locator("#member-tile")
    expect(member.get_by_role("heading", name="Casey Rep")).to_be_visible()
    expect(member).to_contain_text("Attendance: 100.0% (1/1 votes)")
    expect(member.locator(".vote-position")).to_have_text("Voted: Yea")
    expect(member.get_by_role("button", name="Example Act")).to_be_visible()


def test_choosing_a_vote_opens_its_bill(page: Page):
    look_up_the_capitol(page)
    page.get_by_role("button", name="Casey Rep").click()

    page.get_by_role("button", name="Example Act").click()

    bill = page.locator("#bill-tile")
    expect(bill.get_by_role("heading", name="Example Act")).to_be_visible()
    expect(bill).to_contain_text("Does a thing.")


def test_how_the_bill_moved_opens_with_a_click(page: Page):
    look_up_the_capitol(page)
    page.get_by_role("button", name="Casey Rep").click()
    page.get_by_role("button", name="Example Act").click()

    bill = page.locator("#bill-tile")
    voice_vote = bill.get_by_text("Passed/agreed to in House: By voice vote.")
    expect(voice_vote).to_be_hidden()

    bill.get_by_text("How this bill moved").click()

    expect(voice_vote).to_be_visible()


def test_choosing_another_member_closes_the_open_bill(page: Page):
    look_up_the_capitol(page)
    page.get_by_role("button", name="Casey Rep").click()
    page.get_by_role("button", name="Example Act").click()
    expect(page.locator("#bill-tile")).not_to_be_empty()

    page.get_by_role("button", name="Alex Senator").click()

    # The member's name is the tile's one level-2 heading, its <h2>.
    member_name = page.locator("#member-tile").get_by_role("heading", level=2)
    expect(member_name).to_have_text("Alex Senator")
    expect(page.locator("#bill-tile")).to_be_empty()


def test_clear_empties_the_fields_and_every_tile(page: Page):
    look_up_the_capitol(page)
    page.get_by_role("button", name="Casey Rep").click()
    page.get_by_role("button", name="Example Act").click()
    expect(page.locator("#bill-tile")).not_to_be_empty()

    page.get_by_role("button", name="Clear").click()

    for label in ("Street address", "City", "State", "ZIP code"):
        expect(page.get_by_label(label)).to_have_value("")
    for tile in ("#officials-tile", "#member-tile", "#bill-tile"):
        expect(page.locator(tile)).to_be_empty()


def test_scrolling_past_the_top_of_a_tile_moves_the_page(page: Page):
    look_up_the_capitol(page)
    page.get_by_role("button", name="Casey Rep").click()
    page.get_by_role("button", name="Example Act").click()
    bill = page.locator("#bill-tile")
    expect(bill.get_by_role("heading", name="Example Act")).to_be_visible()

    # The made-up bill is short, so give the tile enough to scroll, then
    # start with the tile at its top and the page scrolled down to it, as it
    # is after a bill opens.
    bill.evaluate(
        """tile => {
            tile.insertAdjacentHTML("beforeend", '<div style="height: 200vh"></div>');
            tile.scrollTop = 0;
            tile.scrollIntoView();
        }"""
    )
    page_top = page.evaluate("window.scrollY")
    assert page_top > 0

    bill.hover()
    page.mouse.wheel(0, -200)

    page.wait_for_function("top => window.scrollY < top", arg=page_top)
    assert bill.evaluate("tile => tile.scrollTop") == 0


def test_typing_in_the_search_box_narrows_the_votes(page: Page):
    look_up_the_capitol(page)
    page.get_by_role("button", name="Casey Rep").click()
    member = page.locator("#member-tile")
    search = member.get_by_label("Search")
    vote = member.get_by_role("button", name="Example Act")
    expect(vote).to_be_visible()

    search.fill("tariffs")
    expect(member.get_by_text("No votes to show.")).to_be_visible()
    expect(vote).to_be_hidden()

    search.fill("")
    expect(vote).to_be_visible()

    # Enter runs the search in place; the page itself does not reload.
    search.fill("example")
    search.press("Enter")
    expect(vote).to_be_visible()
    expect(page).to_have_url(page.url.split("?")[0])
    expect(member.get_by_role("heading", name="Casey Rep")).to_be_visible()
