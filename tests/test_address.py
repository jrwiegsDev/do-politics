"""Tests for the rules an address must meet. No database or network is needed."""

import pytest
from pydantic import ValidationError

from app.address import Address


def test_street_with_a_zip_code_is_enough():
    address = Address(street="401 S 2nd St", zip_code="62701")

    assert address.city == ""
    assert address.state == ""


def test_street_with_a_city_and_state_is_enough():
    address = Address(street="401 S 2nd St", city="Springfield", state="IL")

    assert address.zip_code == ""


def test_spaces_around_a_field_are_removed():
    address = Address(street="  401 S 2nd St ", city=" Springfield ", state=" IL ")

    assert (address.street, address.city, address.state) == (
        "401 S 2nd St",
        "Springfield",
        "IL",
    )


def test_state_is_stored_in_capitals():
    address = Address(street="401 S 2nd St", city="Springfield", state="il")

    assert address.state == "IL"


@pytest.mark.parametrize(
    "fields",
    [
        {"street": "401 S 2nd St"},
        {"street": "401 S 2nd St", "city": "Springfield"},
        {"street": "401 S 2nd St", "state": "IL"},
        {"street": "", "zip_code": "62701"},
        {"street": "x", "zip_code": "62701"},
        {"street": "   ", "zip_code": "62701"},
        {"street": "401 S 2nd St", "zip_code": "6270"},
        {"street": "401 S 2nd St", "zip_code": "62701-1234"},
        {"street": "401 S 2nd St", "zip_code": "abcde"},
        {"street": "401 S 2nd St", "city": "Springfield", "state": "Illinois"},
        {"street": "401 S 2nd St", "city": "Springfield", "state": "I1"},
        {"street": "4" * 101, "zip_code": "62701"},
        {"zip_code": "62701"},
    ],
)
def test_an_address_that_breaks_a_rule_is_refused(fields):
    with pytest.raises(ValidationError):
        Address(**fields)
