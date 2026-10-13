"""Turn the HTML of an official bill summary into plain text."""

import re

from bs4 import BeautifulSoup


def html_to_text(html: str) -> str:
    """Return a summary as plain text, keeping its paragraphs and list items.

    Paragraphs and lists are set apart by a blank line and each list item
    gets its own line, starting with a dash. Bold, italics and link
    addresses are dropped.
    """
    soup = BeautifulSoup(" ".join(html.split()), "html.parser")

    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for item in soup.find_all("li"):
        item.insert_before("\n- ")
    for block in soup.find_all(["p", "ul"]):
        block.insert_before("\n\n")
        block.insert_after("\n\n")

    lines = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
