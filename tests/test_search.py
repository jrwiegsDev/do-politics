"""Tests for reading the vote search box into a bill number or words."""

import pytest

from app.search import read_search

NOTHING = {"bill_type": None, "bill_number": None, "words": None}


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_an_empty_search_asks_for_nothing(text):
    assert read_search(text) == NOTHING


@pytest.mark.parametrize(
    ("text", "bill_type", "bill_number"),
    [
        ("H.R. 2102", "hr", 2102),
        ("HR 2102", "hr", 2102),
        ("hr2102", "hr", 2102),
        (" h.r.  2102 ", "hr", 2102),
        ("S. 12", "s", 12),
        ("S.J.Res. 12", "sjres", 12),
        ("H.Res. 1247", "hres", 1247),
    ],
)
def test_a_bill_as_congress_writes_it_gives_its_type_and_number(
    text, bill_type, bill_number
):
    assert read_search(text) == NOTHING | {
        "bill_type": bill_type,
        "bill_number": bill_number,
    }


def test_a_number_alone_gives_only_the_number():
    assert read_search("2102") == NOTHING | {"bill_number": 2102}


@pytest.mark.parametrize(
    "text", ["veterans", "Major Richard Star", "Act 2025", "water -tax"]
)
def test_anything_else_is_words(text):
    assert read_search(text) == NOTHING | {"words": text}


def test_extra_spaces_in_words_are_closed_up():
    assert read_search("  clean   water  ") == NOTHING | {"words": "clean water"}
