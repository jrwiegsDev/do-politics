"""Browser tests: Bill Search finds a bill and opens it, with no address needed."""

import pytest
from playwright.sync_api import Page, expect

# The made-up officials fixture also stores the made-up bill, Example Act.
pytestmark = pytest.mark.usefixtures("illinois_officials")


def test_bill_search_finds_a_bill_and_opens_it_without_an_address(page: Page):
    page.goto("/")

    page.get_by_label("Find a bill").fill("example")

    results = page.locator("#bill-results")
    expect(results).to_contain_text("H.R. 9999 · 119th Congress")
    results.get_by_role("button", name="Example Act").click()

    bill = page.locator("#bill-tile")
    expect(bill.get_by_role("heading", name="Example Act")).to_be_visible()
    expect(bill).to_contain_text("Does a thing.")
    expect(bill).not_to_contain_text("How Casey Rep voted")
    # No address was looked up, so the tiles for officials stay empty.
    expect(page.locator("#officials-tile")).to_be_empty()


def test_clearing_the_search_box_hides_the_results(page: Page):
    page.goto("/")
    search = page.get_by_label("Find a bill")
    search.fill("example")
    expect(page.locator("#bill-results")).to_contain_text("Example Act")

    search.fill("")

    expect(page.locator("#bill-results")).to_be_empty()
    expect(page.locator("#bill-results")).to_be_hidden()


def test_a_search_that_finds_nothing_says_so(page: Page):
    page.goto("/")

    page.get_by_label("Find a bill").fill("tariffs")

    expect(page.locator("#bill-results")).to_have_text("No bills match that search.")
