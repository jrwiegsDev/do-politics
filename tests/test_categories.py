"""Tests for deciding what kind of vote a roll call is. No database or network."""

import pytest

from ingest.categories import categorize, is_known


def vote(chamber, question, title=None):
    """The three fields the rules read, as a parsed vote would hold them."""
    return {"chamber": chamber, "question": question, "title": title}


@pytest.mark.parametrize(
    "question",
    [
        "On Passage",
        "On Motion to Suspend the Rules and Pass, as Amended",
        "On Motion to Concur in the Senate Amendment",
        "Passage, Objections of the President To The Contrary Notwithstanding",
    ],
)
def test_house_final_votes_are_legislation(question):
    assert categorize(vote("house", question)) == "legislation"


@pytest.mark.parametrize(
    "question",
    [
        "On Agreeing to the Amendment",
        "On Motion to Recommit",
        "On Ordering the Previous Question",
        "Call of the House",
    ],
)
def test_house_steps_along_the_way_are_procedural(question):
    assert categorize(vote("house", question)) == "procedural"


def test_house_resolution_is_legislation():
    title = (
        "Directing the President, pursuant to section 5(c) of the War Powers Resolution"
    )

    assert (
        categorize(vote("house", "On Agreeing to the Resolution", title))
        == "legislation"
    )


def test_house_rule_for_debating_a_bill_is_procedural():
    title = "Providing for consideration of the bill (H.R. 9999) to do something"

    assert (
        categorize(vote("house", "On Agreeing to the Resolution", title))
        == "procedural"
    )


def test_house_resolution_with_no_title_is_legislation():
    assert categorize(vote("house", "On Agreeing to the Resolution")) == "legislation"


def test_senate_confirmation_is_a_nomination():
    assert categorize(vote("senate", "On the Nomination")) == "nomination"


@pytest.mark.parametrize(
    "question",
    ["On Passage of the Bill", "On the Joint Resolution", "On the Resolution"],
)
def test_senate_final_votes_are_legislation(question):
    assert categorize(vote("senate", question)) == "legislation"


@pytest.mark.parametrize(
    "question",
    ["On the Cloture Motion", "On the Motion to Proceed", "On the Amendment"],
)
def test_senate_steps_along_the_way_are_procedural(question):
    assert categorize(vote("senate", question)) == "procedural"


def test_senate_motion_to_accept_the_house_version_is_legislation():
    title = "Motion to Concur in the House Amendment to S. 9999"

    assert categorize(vote("senate", "On the Motion", title)) == "legislation"


def test_senate_motion_to_waive_budget_rules_is_procedural():
    title = "Motion to Waive All Applicable Budgetary Discipline Re: Amdt. No. 99"

    assert categorize(vote("senate", "On the Motion", title)) == "procedural"


def test_the_same_words_in_the_other_chamber_do_not_count():
    assert categorize(vote("senate", "On Passage")) == "procedural"


def test_a_wording_never_seen_is_procedural_and_reported_as_unknown():
    unseen = vote("senate", "On the Conference Report")

    assert categorize(unseen) == "procedural"
    assert not is_known(unseen)


def test_a_vote_with_no_question_is_procedural_and_unknown():
    blank = vote("house", None)

    assert categorize(blank) == "procedural"
    assert not is_known(blank)


def test_a_listed_wording_is_known():
    assert is_known(vote("house", "On Motion to Recommit"))
    assert is_known(vote("senate", "On the Motion"))
