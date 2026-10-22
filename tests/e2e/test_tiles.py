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
