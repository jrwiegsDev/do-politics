"""Browser tests: the security headers block nothing the site itself needs."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.usefixtures("illinois_officials", "census_finds_the_capitol")


def collect_violations(page: Page) -> list[str]:
    """Record every console message where the browser refused something under the policy."""
    violations: list[str] = []
    page.on(
        "console",
        lambda message: (
            violations.append(message.text)
            if "Content Security Policy" in message.text
            else None
        ),
    )
    return violations


def test_the_whole_tile_flow_runs_under_the_strict_policy(page: Page):
    violations = collect_violations(page)

    page.goto("/")
    page.get_by_label("Street address").fill("401 S 2nd St")
    page.get_by_label("City").fill("Springfield")
    page.get_by_label("State").fill("IL")
    page.get_by_label("ZIP code").fill("62701")
    page.get_by_role("button", name="Find my officials").click()
    page.get_by_role("button", name="Casey Rep").click()
    page.get_by_role("button", name="Example Act").click()
    expect(
        page.locator("#bill-tile").get_by_role("heading", name="Example Act")
    ).to_be_visible()
    page.locator("#member-tile").get_by_label("Search").fill("example")
    expect(
        page.locator("#member-tile").get_by_role("button", name="Example Act")
    ).to_be_visible()

    assert violations == []


@pytest.mark.parametrize("path", ["/privacy", "/terms"])
def test_the_policy_pages_run_under_the_strict_policy(page: Page, path):
    violations = collect_violations(page)

    page.goto(path)
    expect(page.get_by_role("heading", level=2)).to_be_visible()

    assert violations == []


@pytest.mark.parametrize(
    ("path", "ready"),
    [("/docs", ".swagger-ui .info"), ("/redoc", "redoc-wrap, .redoc-wrap")],
)
def test_the_api_docs_still_load(page: Page, path, ready):
    # The docs pages load from a CDN, so this needs the network.
    violations = collect_violations(page)

    page.goto(path)
    expect(page.locator(ready).first).to_be_visible(timeout=15000)

    assert violations == []


def test_an_inline_script_is_refused(page: Page):
    # The proof the policy is on: a script written into the page is blocked.
    violations = collect_violations(page)
    page.goto("/")

    page.evaluate(
        """() => {
            const script = document.createElement("script");
            script.textContent = "window.ran = true";
            document.body.append(script);
        }"""
    )

    assert page.evaluate("window.ran") is None
    assert violations
