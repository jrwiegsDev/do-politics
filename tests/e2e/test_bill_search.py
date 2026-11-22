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


def test_clear_empties_the_search_the_topic_the_results_and_the_bill(page: Page):
    page.goto("/")
    page.get_by_label("Topic").select_option("Energy")
    page.get_by_label("Find a bill").fill("example")
    page.locator("#bill-results").get_by_role("button", name="Example Act").click()
    expect(page.locator("#bill-tile")).not_to_be_empty()

    page.get_by_role("button", name="Clear bill search").click()

    expect(page.get_by_label("Find a bill")).to_have_value("")
    expect(page.get_by_label("Topic")).to_have_value("")
    expect(page.locator("#bill-results")).to_be_empty()
    expect(page.locator("#bill-results")).to_be_hidden()
    expect(page.locator("#bill-tile")).to_be_empty()


@pytest.mark.usefixtures("census_finds_the_capitol")
def test_clearing_the_search_leaves_the_officials_and_the_address_alone(page: Page):
    page.goto("/")
    page.get_by_label("Street address").fill("401 S 2nd St")
    page.get_by_label("ZIP code").fill("62701")
    page.get_by_role("button", name="Find my officials").click()
    expect(page.locator("#officials-tile")).to_contain_text("Casey Rep")
    page.get_by_label("Find a bill").fill("example")
    expect(page.locator("#bill-results")).to_contain_text("Example Act")

    page.get_by_role("button", name="Clear bill search").click()

    expect(page.locator("#bill-results")).to_be_empty()
    expect(page.locator("#officials-tile")).to_contain_text("Casey Rep")
    expect(page.get_by_label("Street address")).to_have_value("401 S 2nd St")


def test_the_address_clear_leaves_the_search_alone(page: Page):
    page.goto("/")
    page.get_by_label("Find a bill").fill("example")
    expect(page.locator("#bill-results")).to_contain_text("Example Act")

    page.locator("#address-tile").get_by_role("button", name="Clear").click()

    expect(page.get_by_label("Find a bill")).to_have_value("example")
    expect(page.locator("#bill-results")).to_contain_text("Example Act")


def test_a_search_that_finds_nothing_says_so(page: Page):
    page.goto("/")

    page.get_by_label("Find a bill").fill("tariffs")

    expect(page.locator("#bill-results")).to_have_text("No bills match that search.")
