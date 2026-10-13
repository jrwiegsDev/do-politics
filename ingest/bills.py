"""Read which bill a vote concerns from the text its chamber gives it.

The House writes 'H R 5103' and the Senate 'H.R. 5103'. Both become the
type and number Congress.gov files the bill under: ('hr', 5103).
"""

import re

BILL_TYPES = {"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"}

# Letters, dots and spaces, then one number: 'H J RES 42', 'S.Con.Res. 7'.
ISSUE_PATTERN = re.compile(r"([A-Za-z. ]+?)\s*(\d+)")


def bill_for(issue: str | None) -> tuple[str, int] | None:
    """Return (bill_type, bill_number) for a vote's issue, or None when it names no bill.

    Nominations ('PN12-1'), quorum calls and votes with no issue are not bills.
    """
    match = ISSUE_PATTERN.fullmatch((issue or "").strip())
    if match is None:
        return None

    letters, number = match.groups()
    bill_type = letters.replace(".", "").replace(" ", "").lower()
    if bill_type not in BILL_TYPES:
        return None
    return bill_type, int(number)
