"""Browser tests: a visitor looks up an address and sees who represents it."""

from playwright.sync_api import Page, expect


def test_an_address_shows_its_senators_and_representative(
    page: Page, illinois_officials, census_finds_the_capitol
):
    page.goto("/")

    page.get_by_label("Street address").fill("401 S 2nd St")
    page.get_by_label("City").fill("Springfield")
    page.get_by_label("State").fill("IL")
    page.get_by_label("ZIP code").fill("62701")
    page.get_by_role("button", name="Find my officials").click()

    officials = page.locator("#officials-tile")
    expect(officials).to_contain_text("Congressional District 13")
    expect(officials.get_by_role("button", name="Alex Senator")).to_be_visible()
    expect(officials.get_by_role("button", name="Blake Senator")).to_be_visible()
    expect(officials.get_by_role("button", name="Casey Rep")).to_be_visible()
