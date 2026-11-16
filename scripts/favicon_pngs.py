"""Draw the PNG copies of the site's icon from app/static/favicon.svg.

Safari, unlike Chrome and Firefox, does not reliably use an SVG favicon, and
iPhones and iPads use a separate "Apple touch icon" for the Home Screen and
for tabs. This renders the SVG in Chromium and saves:

    app/static/favicon-32.png        32x32, with the tile's rounded corners
    app/static/apple-touch-icon.png  180x180, a solid square, as Apple asks

iOS rounds a touch icon's corners itself and fills any transparent pixel
with black, so the touch icon has no transparency, and the drawing sits
inside a margin so the rounding cannot clip it.

Run it after changing favicon.svg:
    uv run python scripts/favicon_pngs.py
"""

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC = Path("app/static")
SVG = STATIC / "favicon.svg"

# The page's paper color, which is also the color of the icon's own tile.
PAPER = "#f6f4ef"

# (file, canvas size in pixels, drawing size, background)
ICONS = [
    ("favicon-32.png", 32, 32, "transparent"),
    ("apple-touch-icon.png", 180, 150, PAPER),
]


def main() -> None:
    data = base64.b64encode(SVG.read_bytes()).decode()
    source = f"data:image/svg+xml;base64,{data}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, canvas, drawing, background in ICONS:
            page = browser.new_page(viewport={"width": canvas, "height": canvas})
            page.set_content(
                f'<body style="margin:0;display:grid;place-items:center;'
                f'width:{canvas}px;height:{canvas}px;background:{background}">'
                f'<img src="{source}" width="{drawing}" height="{drawing}"></body>'
            )
            page.screenshot(
                path=STATIC / name, omit_background=background == "transparent"
            )
            page.close()
            print(f"Saved {STATIC / name}")
        browser.close()


if __name__ == "__main__":
    main()
