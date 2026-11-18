"""Take the screenshot used in the README and on the portfolio card.

Opens the site in a real Chromium, looks up the Illinois State Capitol,
opens the representative's tile and saves the whole page as a PNG, from
the title to the footer's links.

Run it while the dev server is up, or point it at the live site:
    uv run python scripts/screenshot.py
    uv run python scripts/screenshot.py https://dopolitics.org
"""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUTPUT = Path("docs/screenshot.png")


def main() -> None:
    parser = argparse.ArgumentParser(description="Screenshot DoPolitics.")
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=2,
        )
        page.goto(args.base_url)

        page.get_by_label("Street address").fill("401 S 2nd St")
        page.get_by_label("City").fill("Springfield")
        page.get_by_label("State").fill("IL")
        page.get_by_label("ZIP code").fill("62701")
        page.get_by_role("button", name="Find my officials").click()

        # The representative is listed last. Picking by position, not by
        # name, keeps the script working when the seat changes hands.
        officials = page.locator("#officials-tile")
        officials.get_by_role("button").last.click()

        member = page.locator("#member-tile")
        expect(member.get_by_role("heading", name="Votes")).to_be_visible()
        page.wait_for_load_state("networkidle")

        # The whole page, not just the window: with Bill Search under the
        # address form, the title and the footer no longer fit in one window.
        page.screenshot(path=OUTPUT, full_page=True)
        browser.close()

    print(f"Saved {OUTPUT}")


if __name__ == "__main__":
    main()
