"""Tests for looking up a member's recent votes."""

from helpers import add_bill, add_members, add_vote

from app.votes import (
    ALL_CATEGORIES,
    find_attendance,
    find_policy_areas,
    find_recent_votes,
)

PAT = "T000001"
SAM = "T000002"


def rolls(votes):
    """Return just the roll numbers, in the order the lookup gave them."""
    return [vote["roll_number"] for vote in votes]


def test_votes_come_newest_first(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 3, day=20, positions={PAT: "Nay"})
    add_vote(conn, 2, day=10, positions={PAT: "Yea"})

    assert rolls(find_recent_votes(conn, PAT)) == [3, 2, 1]


def test_two_votes_at_the_same_moment_come_highest_roll_number_first(conn):
    add_members(conn)
    add_vote(conn, 7, day=5, positions={PAT: "Yea"})
    add_vote(conn, 8, day=5, positions={PAT: "Yea"})

    assert rolls(find_recent_votes(conn, PAT)) == [8, 7]


def test_limit_keeps_only_the_newest(conn):
    add_members(conn)
    for roll_number in (1, 2, 3):
        add_vote(conn, roll_number, day=roll_number, positions={PAT: "Yea"})

    assert rolls(find_recent_votes(conn, PAT, limit=2)) == [3, 2]


def test_each_vote_carries_the_members_own_position(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea", SAM: "Nay"})

    assert [vote["position"] for vote in find_recent_votes(conn, PAT)] == ["Yea"]
    assert [vote["position"] for vote in find_recent_votes(conn, SAM)] == ["Nay"]


def test_a_vote_the_member_took_no_part_in_is_left_out(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={SAM: "Yea"})

    assert rolls(find_recent_votes(conn, PAT)) == [1]


def test_procedural_votes_are_left_out_by_default(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, question="On Motion to Recommit")

    assert rolls(find_recent_votes(conn, PAT)) == [1]


def test_confirmations_are_included_by_default(conn):
    add_members(conn)
    add_vote(
        conn,
        1,
        day=5,
        positions={PAT: "Yea"},
        question="On the Nomination",
        chamber="senate",
    )

    votes = find_recent_votes(conn, PAT)

    assert [vote["category"] for vote in votes] == ["nomination"]


def test_every_category_can_be_asked_for(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, question="On Motion to Recommit")

    assert rolls(find_recent_votes(conn, PAT, categories=ALL_CATEGORIES)) == [2, 1]


def test_member_with_no_votes_gets_an_empty_list(conn):
    add_members(conn)

    assert find_recent_votes(conn, PAT) == []


def test_a_vote_on_a_stored_bill_carries_the_bill(conn):
    add_members(conn)
    add_bill(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})

    vote = find_recent_votes(conn, PAT)[0]

    assert vote["bill"] == {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 9999,
        "title": "Example Act",
        "policy_area": "Energy",
        "source_url": "https://example.test/bill/9999",
    }


def test_a_vote_on_a_bill_that_is_not_stored_has_no_bill(conn):
    add_members(conn)
    add_bill(conn, 1)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})

    vote = find_recent_votes(conn, PAT)[0]

    assert vote["bill"] is None


def test_the_bill_does_not_change_the_votes_own_title_or_link(conn):
    add_members(conn)
    add_bill(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, title="On passing it")

    vote = find_recent_votes(conn, PAT)[0]

    assert vote["title"] == "On passing it"
    assert vote["source_url"] == "https://example.test/house/1"
    assert not [key for key in vote if key.startswith("bill_")]


def test_a_stored_bill_does_not_repeat_or_drop_votes(conn):
    add_members(conn)
    add_bill(conn)
    add_bill(conn, 1)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={PAT: "Nay"})

    assert rolls(find_recent_votes(conn, PAT)) == [2, 1]


def test_offset_skips_the_newest_votes(conn):
    add_members(conn)
    for roll_number in (1, 2, 3, 4):
        add_vote(conn, roll_number, day=roll_number, positions={PAT: "Yea"})

    assert rolls(find_recent_votes(conn, PAT, limit=2, offset=2)) == [2, 1]


def test_offset_past_the_last_vote_gives_an_empty_list(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})

    assert find_recent_votes(conn, PAT, offset=1) == []


def test_naming_a_policy_area_keeps_only_votes_on_bills_in_it(conn):
    add_members(conn)
    add_bill(conn, 9999, policy_area="Energy")
    add_bill(conn, 8888, policy_area="Health")
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, issue="H R 8888")
    add_vote(conn, 3, day=7, positions={PAT: "Yea"}, issue="H R 9999")

    assert rolls(find_recent_votes(conn, PAT, policy_area="Energy")) == [3, 1]
    assert rolls(find_recent_votes(conn, PAT, policy_area="Health")) == [2]
    assert rolls(find_recent_votes(conn, PAT)) == [3, 2, 1]


def test_naming_a_policy_area_leaves_out_votes_with_no_stored_bill(conn):
    add_members(conn)
    add_bill(conn, 9999, policy_area="Energy")
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, issue="H R 7777")

    assert rolls(find_recent_votes(conn, PAT, policy_area="Energy")) == [1]


def test_policy_areas_come_once_each_in_alphabetical_order(conn):
    add_members(conn)
    add_bill(conn, 9999, policy_area="Health")
    add_bill(conn, 8888, policy_area="Energy")
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 2, day=6, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 3, day=7, positions={PAT: "Yea"}, issue="H R 8888")

    assert find_policy_areas(conn, PAT) == ["Energy", "Health"]


def test_policy_areas_cover_only_the_members_own_final_votes(conn):
    add_members(conn)
    add_bill(conn, 9999, policy_area="Health")
    add_bill(conn, 8888, policy_area="Energy")
    add_bill(conn, 7777, policy_area="Taxation")
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, issue="H R 9999")
    add_vote(conn, 2, day=6, positions={SAM: "Yea"}, issue="H R 8888")
    add_vote(
        conn,
        3,
        day=7,
        positions={PAT: "Yea"},
        issue="H R 7777",
        question="On Motion to Recommit",
    )

    assert find_policy_areas(conn, PAT) == ["Health"]


def test_attendance_counts_everything_but_not_voting_as_taking_part(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={PAT: "Not Voting"})
    add_vote(conn, 3, day=7, positions={PAT: "Present"})
    add_vote(conn, 4, day=8, positions={PAT: "No"}, question="On Motion to Recommit")

    assert find_attendance(conn, PAT) == [
        {"congress": 119, "session": 2, "roll_calls": 4, "votes_cast": 3}
    ]


def test_attendance_has_a_row_for_each_session_newest_first(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, session=1)
    add_vote(conn, 1, day=6, positions={PAT: "Not Voting"}, session=2)
    add_vote(conn, 2, day=7, positions={PAT: "Yea"}, session=2)

    assert find_attendance(conn, PAT) == [
        {"congress": 119, "session": 2, "roll_calls": 2, "votes_cast": 1},
        {"congress": 119, "session": 1, "roll_calls": 1, "votes_cast": 1},
    ]


def test_attendance_leaves_out_roll_calls_that_did_not_list_the_member(conn):
    add_members(conn)
    add_vote(conn, 1, day=5, positions={PAT: "Yea"})
    add_vote(conn, 2, day=6, positions={SAM: "Yea"})

    assert find_attendance(conn, PAT) == [
        {"congress": 119, "session": 2, "roll_calls": 1, "votes_cast": 1}
    ]


def test_member_with_no_roll_calls_has_no_attendance_rows(conn):
    add_members(conn)

    assert find_attendance(conn, PAT) == []
