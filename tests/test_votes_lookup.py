"""Tests for looking up a member's recent votes."""

from app.votes import ALL_CATEGORIES, find_recent_votes
from helpers import add_members, add_vote

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
    add_vote(conn, 1, day=5, positions={PAT: "Yea"}, question="On the Nomination", chamber="senate")

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
