"""Tests for reading a bill's type and number from a vote's issue text."""

import pytest

from ingest.bills import bill_for


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        ("H R 5103", ("hr", 5103)),
        ("H.R. 5103", ("hr", 5103)),
        ("S 2403", ("s", 2403)),
        ("S. 2403", ("s", 2403)),
        ("H J RES 42", ("hjres", 42)),
        ("S.J.Res. 42", ("sjres", 42)),
        ("H CON RES 7", ("hconres", 7)),
        ("S.Con.Res. 7", ("sconres", 7)),
        ("H RES 5", ("hres", 5)),
        ("S.Res. 5", ("sres", 5)),
        ("  H R 1  ", ("hr", 1)),
    ],
)
def test_bill_for_reads_the_type_and_number(issue, expected):
    assert bill_for(issue) == expected


@pytest.mark.parametrize("issue", ["PN12-1", "PN12", "QUORUM", "ADJOURN", "", None])
def test_bill_for_returns_none_when_the_issue_names_no_bill(issue):
    assert bill_for(issue) is None
