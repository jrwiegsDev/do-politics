"""Tests for the terms line: how long a member has served, from their terms.

The cases are real members' terms as congress-legislators lists them.
"""

from datetime import date

from app.service import is_full, service_lines


def term(chamber: str, starts_on: str, ends_on: str) -> dict:
    return {
        "chamber": chamber,
        "starts_on": date.fromisoformat(starts_on),
        "ends_on": date.fromisoformat(ends_on),
    }


def house_terms(first_year: int, last_year: int) -> list[dict]:
    """Full two-year House terms from January 3 of first_year to last_year."""
    return [
        term("house", f"{year}-01-03", f"{year + 2}-01-03")
        for year in range(first_year, last_year, 2)
    ]


def senate_terms(first_year: int, count: int) -> list[dict]:
    """Full six-year Senate terms from January 3 of first_year."""
    return [
        term("senate", f"{first_year + 6 * n}-01-03", f"{first_year + 6 * n + 6}-01-03")
        for n in range(count)
    ]


def test_durbin_seven_house_terms_then_his_fifth_senate_term():
    terms = house_terms(1983, 1997) + senate_terms(1997, 5)

    assert service_lines(terms) == [
        "Senate: 5th term (since 1997)",
        "House: 7 terms (1983–1997)",
    ]


def test_duckworth_two_house_terms_then_her_second_senate_term():
    terms = house_terms(2013, 2017) + senate_terms(2017, 2)

    assert service_lines(terms) == [
        "Senate: 2nd term (since 2017)",
        "House: 2 terms (2013–2017)",
    ]


def test_one_chamber_gives_one_line():
    assert service_lines(house_terms(2023, 2027)) == ["House: 2nd term (since 2023)"]


def test_one_past_term_is_singular():
    terms = house_terms(1993, 1995) + senate_terms(2001, 5)

    assert service_lines(terms)[1] == "House: 1 term (1993–1995)"


def test_a_partial_term_sets_since_but_is_not_counted_and_says_full():
    # Tim Scott: appointed in 2013, then a two-year remainder the file does not
    # label, then two full terms.
    terms = house_terms(2011, 2013) + [
        term("senate", "2013-01-02", "2015-01-03"),
        term("senate", "2015-01-06", "2017-01-03"),
        *senate_terms(2017, 2),
    ]

    assert service_lines(terms)[0] == "Senate: 2nd full term (since 2013)"


def test_a_short_special_election_term_before_a_first_full_term():
    # Adam Schiff: a December 2024 special election for one month, then a full term.
    terms = house_terms(2001, 2025) + [
        term("senate", "2024-12-09", "2025-01-03"),
        term("senate", "2025-01-03", "2031-01-03"),
    ]

    assert service_lines(terms)[0] == "Senate: 1st full term (since 2024)"


def test_a_member_serving_only_a_partial_term_is_filling_a_vacant_seat():
    # Pete Ricketts: appointed, then elected for the rest of the term.
    terms = [
        term("senate", "2023-01-23", "2024-11-05"),
        term("senate", "2024-11-05", "2027-01-03"),
    ]

    assert service_lines(terms) == ["Senate: filling a vacant seat (since 2023)"]


def test_a_return_after_years_away_lists_both_stretches():
    # Ed Case: the House from 2003 to 2007, then again from 2019.
    terms = house_terms(2003, 2007) + house_terms(2019, 2027)

    assert service_lines(terms) == ["House: 6th term (since 2019; also 2003–2007)"]


def test_a_past_chamber_with_a_gap_lists_each_stretch():
    terms = house_terms(1983, 1987) + house_terms(1991, 1993) + senate_terms(1997, 1)

    assert service_lines(terms)[1] == "House: 3 terms (1983–1987, 1991–1993)"


def test_a_short_gap_is_not_a_break():
    # In 1979 the 96th Congress convened on January 15, not January 3.
    terms = [
        term("house", "1977-01-04", "1979-01-03"),
        term("house", "1979-01-15", "1981-01-03"),
        term("house", "1981-01-05", "1983-01-03"),
    ]

    assert service_lines(terms) == ["House: 3rd term (since 1977)"]


def test_a_past_chamber_with_only_a_partial_term():
    terms = [term("house", "2022-11-08", "2023-01-03"), *senate_terms(2023, 1)]

    assert service_lines(terms)[1] == "House: part of a term (2022–2023)"


def test_a_full_term_may_start_a_few_days_late():
    # Congress sometimes convenes on January 5 or 7.
    assert is_full(term("house", "1987-01-06", "1989-01-03"))
    assert is_full(term("senate", "1997-01-07", "2003-01-03"))
    assert not is_full(term("house", "2022-11-08", "2023-01-03"))
    assert not is_full(term("senate", "2013-01-02", "2015-01-03"))


def test_no_terms_gives_no_lines():
    assert service_lines([]) == []
