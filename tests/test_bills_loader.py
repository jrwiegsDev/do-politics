"""Tests for the bills loader.

The records below are made up, in the layout Congress.gov really sends, so
no test calls the API and none needs a key.
"""

import io
import urllib.error
from datetime import UTC, date, datetime, time
from email.message import Message

import psycopg
import pytest
from helpers import add_members, add_vote, last_run

from ingest.bills import (
    fetch_json,
    load,
    read_bill,
    save,
    to_action_rows,
    to_bill_row,
    to_summary_row,
)

BILL = {
    "congress": 119,
    "type": "HR",
    "number": "9999",
    "title": "Example Act",
    "policyArea": {"name": "Energy"},
    "sponsors": [{"bioguideId": "T000001", "fullName": "Rep. Example, Pat [I-IL-13]"}],
    "introducedDate": "2025-01-15",
    "latestAction": {"actionDate": "2025-05-09", "text": "Became Public Law."},
    "legislationUrl": "https://www.congress.gov/bill/119th-congress/house-bill/9999",
    "summaries": {"count": 2},
    "updateDate": "2026-09-19T23:07:08Z",
}

SUMMARIES = [
    {
        "versionCode": "00",
        "actionDate": "2025-01-15",
        "actionDesc": "Introduced in House",
        "text": "<p>This bill does <em>one</em> thing.</p>",
        "updateDate": "2025-01-27T14:02:08Z",
    },
    {
        "versionCode": "49",
        "actionDate": "2025-05-09",
        "actionDesc": "Public Law",
        "text": "<p>This act does two things.</p><ul><li>One.</li><li>Two.</li></ul>",
        "updateDate": "2026-06-05T14:52:33Z",
    },
]


# Newest first, as Congress.gov lists them. The two referrals are identical,
# as they are when a bill goes to two committees. The newest has no time
# and no code, as a Senate action often has neither.
ACTIONS = [
    {
        "actionDate": "2025-03-03",
        "type": "IntroReferral",
        "text": "Received in the Senate.",
        "sourceSystem": {"code": 0, "name": "Senate"},
    },
    {
        "actionDate": "2025-03-02",
        "actionTime": "10:59:28",
        "actionCode": "8000",
        "type": "Floor",
        "text": "Passed/agreed to in House: On passage Agreed to by voice vote.",
        "sourceSystem": {"code": 9, "name": "Library of Congress"},
    },
    {
        "actionDate": "2025-01-15",
        "actionCode": "H11100",
        "type": "IntroReferral",
        "text": "Referred to the Committee on Energy and Commerce.",
        "sourceSystem": {"code": 2, "name": "House floor actions"},
    },
    {
        "actionDate": "2025-01-15",
        "actionCode": "H11100",
        "type": "IntroReferral",
        "text": "Referred to the Committee on Energy and Commerce.",
        "sourceSystem": {"code": 2, "name": "House floor actions"},
    },
]

# The made-up bill, saying it has the four made-up actions.
WITH_ACTIONS = BILL | {"actions": {"count": 4}}


class FakeApi:
    """Stand in for fetch_json, and remember each address that was asked for.

    Any bill asked for is answered with the made-up bill under that number,
    its summaries with the made-up summaries, and its actions with the
    made-up actions. The query parameters of each request are kept in
    params. Given a status, every request fails with that HTTP status
    instead.
    """

    def __init__(self, bill=BILL, summaries=SUMMARIES, actions=ACTIONS, status=None):
        self.bill = bill
        self.summaries = summaries
        self.actions = actions
        self.status = status
        self.asked = []
        self.params = []

    def __call__(self, path: str, params: dict | None = None) -> dict:
        self.asked.append(path)
        self.params.append(params)
        if self.status is not None:
            raise urllib.error.HTTPError(
                f"https://example.test/{path}", self.status, "Refused", Message(), None
            )
        if path.endswith("/summaries"):
            return {"summaries": self.summaries}
        if path.endswith("/actions"):
            return {"actions": self.actions}
        return {"bill": self.bill | {"number": path.split("/")[3]}}


def use(monkeypatch, api: FakeApi) -> FakeApi:
    """Put the stand-in where the loader looks for fetch_json."""
    monkeypatch.setattr("ingest.bills.fetch_json", api)
    return api


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def rows_for(summaries: list[dict]) -> list[dict]:
    return [to_summary_row(summary) for summary in summaries]


@pytest.fixture
def no_waiting(monkeypatch):
    """Skip the pause between requests, since nothing is really requested."""
    monkeypatch.setattr("ingest.bills.sleep", lambda seconds: None)


def test_bill_record_becomes_a_row():
    assert to_bill_row(BILL) == {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 9999,
        "title": "Example Act",
        "policy_area": "Energy",
        "sponsor_bioguide_id": "T000001",
        "introduced_on": date(2025, 1, 15),
        "latest_action_on": date(2025, 5, 9),
        "latest_action": "Became Public Law.",
        "source_url": "https://www.congress.gov/bill/119th-congress/house-bill/9999",
        "source_updated_at": datetime(2026, 9, 19, 23, 7, 8, tzinfo=UTC),
    }


def test_bill_with_no_policy_area_or_latest_action_leaves_them_empty():
    new_bill = {
        key: value
        for key, value in BILL.items()
        if key not in ("policyArea", "latestAction")
    }

    row = to_bill_row(new_bill)

    assert row["policy_area"] is None
    assert row["latest_action_on"] is None
    assert row["latest_action"] is None


def test_bill_with_no_title_is_refused():
    untitled = {key: value for key, value in BILL.items() if key != "title"}

    with pytest.raises(KeyError):
        to_bill_row(untitled)


def test_bill_with_no_sponsor_is_refused():
    with pytest.raises(IndexError):
        to_bill_row(BILL | {"sponsors": []})


def test_summary_becomes_a_row_holding_the_text_and_the_original():
    assert to_summary_row(SUMMARIES[0]) == {
        "version_code": "00",
        "action_on": date(2025, 1, 15),
        "action_desc": "Introduced in House",
        "summary_text": "This bill does one thing.",
        "summary_html_source": "<p>This bill does <em>one</em> thing.</p>",
        "source_updated_at": datetime(2025, 1, 27, 14, 2, 8, tzinfo=UTC),
    }


def test_read_bill_returns_the_bill_and_each_summary(monkeypatch):
    api = use(monkeypatch, FakeApi())

    bill, summaries, _ = read_bill(119, "hr", 9999)

    assert bill["title"] == "Example Act"
    assert [summary["version_code"] for summary in summaries] == ["00", "49"]
    assert api.asked == ["bill/119/hr/9999", "bill/119/hr/9999/summaries"]


def test_read_bill_does_not_ask_for_summaries_when_the_bill_has_none(monkeypatch):
    api = use(monkeypatch, FakeApi(bill=BILL | {"summaries": {"count": 0}}))

    _, summaries, _ = read_bill(119, "hr", 9999)

    assert summaries == []
    assert api.asked == ["bill/119/hr/9999"]


def test_read_bill_refuses_a_record_for_a_different_bill(monkeypatch):
    use(monkeypatch, FakeApi())

    with pytest.raises(ValueError, match="expected"):
        read_bill(119, "s", 9999)


def test_read_bill_refuses_a_list_shorter_than_the_bill_says(monkeypatch):
    use(monkeypatch, FakeApi(bill=BILL | {"summaries": {"count": 3}}))

    with pytest.raises(ValueError, match="only 2 were sent"):
        read_bill(119, "hr", 9999)


def test_saving_a_bill_twice_keeps_one_bill_and_one_summary_per_version(conn):
    add_members(conn)

    save(conn, to_bill_row(BILL), rows_for(SUMMARIES), [])
    save(conn, to_bill_row(BILL), rows_for(SUMMARIES), [])

    assert count(conn, "bills") == 1
    assert count(conn, "bill_summaries") == 2


def test_saving_a_revised_summary_replaces_its_text(conn):
    add_members(conn)
    save(conn, to_bill_row(BILL), rows_for(SUMMARIES), [])

    revised = SUMMARIES[0] | {"text": "<p>This bill now does more.</p>"}
    save(conn, to_bill_row(BILL), rows_for([revised]), [])

    text = conn.execute(
        "SELECT summary_text FROM bill_summaries WHERE version_code = '00'"
    ).fetchone()[0]

    assert text == "This bill now does more."
    assert count(conn, "bill_summaries") == 2


def test_bill_whose_sponsor_is_not_a_known_member_is_refused(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        save(conn, to_bill_row(BILL), [], [])


def test_deleting_a_bill_deletes_its_summaries(conn):
    add_members(conn)
    save(conn, to_bill_row(BILL), rows_for(SUMMARIES), [])

    conn.execute("DELETE FROM bills")

    assert count(conn, "bill_summaries") == 0


def test_load_saves_each_bill_once_however_many_votes_name_it(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    add_vote(committing_conn, 2, 6, {"T000001": "Nay"})
    api = use(monkeypatch, FakeApi())

    loaded, missing = load(committing_conn)

    recorded = last_run(committing_conn)
    assert (loaded, missing) == (1, [])
    assert count(committing_conn, "bills") == 1
    assert count(committing_conn, "bill_summaries") == 2
    assert api.asked == ["bill/119/hr/9999", "bill/119/hr/9999/summaries"]
    assert (recorded["job"], recorded["status"]) == ("bills", "succeeded")
    assert recorded["rows_loaded"] == 1


def test_load_reports_and_skips_a_bill_that_is_not_found(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi(status=404))

    loaded, missing = load(committing_conn)

    assert (loaded, missing) == (0, ["119 hr 9999"])
    assert last_run(committing_conn)["status"] == "succeeded"


def test_load_fails_the_run_on_any_other_refusal(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi(status=403))

    with pytest.raises(urllib.error.HTTPError):
        load(committing_conn)

    recorded = last_run(committing_conn)
    assert recorded["status"] == "failed"
    assert recorded["error"] == "HTTPError: HTTP Error 403: Refused"


def test_load_stops_at_the_limit(committing_conn, monkeypatch, no_waiting):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    add_vote(committing_conn, 2, 6, {"T000001": "Yea"})
    committing_conn.execute("UPDATE votes SET bill_number = 1 WHERE roll_number = 2")
    use(monkeypatch, FakeApi())

    loaded, _ = load(committing_conn, limit=1)

    assert loaded == 1
    assert count(committing_conn, "bills") == 1


def test_actions_become_rows_oldest_first():
    rows = to_action_rows(ACTIONS)

    assert [row["position"] for row in rows] == [0, 1, 2, 3]
    assert rows[2] == {
        "position": 2,
        "acted_on": date(2025, 3, 2),
        "acted_at": time(10, 59, 28),
        "action_code": "8000",
        "action_type": "Floor",
        "source": "Library of Congress",
        "action_text": "Passed/agreed to in House: On passage Agreed to by voice vote.",
    }
    assert rows[3]["action_text"] == "Received in the Senate."


def test_action_with_no_time_or_code_leaves_them_empty():
    row = to_action_rows(ACTIONS[:1])[0]

    assert row["acted_at"] is None
    assert row["action_code"] is None


def test_action_with_no_words_is_refused():
    wordless = {key: value for key, value in ACTIONS[0].items() if key != "text"}

    with pytest.raises(KeyError):
        to_action_rows([wordless])


def test_read_bill_asks_for_up_to_250_actions_in_one_request(monkeypatch):
    api = use(monkeypatch, FakeApi(bill=WITH_ACTIONS))

    _, _, actions = read_bill(119, "hr", 9999)

    assert len(actions) == 4
    assert api.asked[-1] == "bill/119/hr/9999/actions"
    assert api.params[-1] == {"limit": 250}


def test_read_bill_does_not_ask_for_actions_when_the_bill_has_none(monkeypatch):
    api = use(monkeypatch, FakeApi(bill=BILL | {"actions": {"count": 0}}))

    _, _, actions = read_bill(119, "hr", 9999)

    assert actions == []
    assert "bill/119/hr/9999/actions" not in api.asked


def test_read_bill_refuses_an_action_list_shorter_than_the_bill_says(monkeypatch):
    use(monkeypatch, FakeApi(bill=BILL | {"actions": {"count": 5}}))

    with pytest.raises(ValueError, match="5 actions but only 4 were sent"):
        read_bill(119, "hr", 9999)


def test_read_bill_asks_for_actions_even_when_the_bill_has_no_summaries(monkeypatch):
    bill = WITH_ACTIONS | {"summaries": {"count": 0}}
    api = use(monkeypatch, FakeApi(bill=bill))

    _, summaries, actions = read_bill(119, "hr", 9999)

    assert (len(summaries), len(actions)) == (0, 4)
    assert api.asked == ["bill/119/hr/9999", "bill/119/hr/9999/actions"]


def stored_actions(conn) -> list[tuple]:
    return conn.execute(
        "SELECT position, action_text FROM bill_actions ORDER BY position"
    ).fetchall()


def test_two_identical_actions_are_both_kept(conn):
    add_members(conn)

    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS))

    texts = [text for _, text in stored_actions(conn)]
    assert texts.count("Referred to the Committee on Energy and Commerce.") == 2


def test_saving_a_bill_twice_keeps_one_copy_of_its_actions(conn):
    add_members(conn)

    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS))
    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS))

    assert count(conn, "bill_actions") == 4


def test_saving_a_bill_replaces_its_actions_with_the_new_list(conn):
    # Congress.gov sometimes rewrites or drops an action. The stored list
    # becomes exactly the new one, with nothing left over from the old.
    add_members(conn)
    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS))

    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS[1:2]))

    assert stored_actions(conn) == [
        (0, "Passed/agreed to in House: On passage Agreed to by voice vote.")
    ]


def test_saving_one_bill_leaves_another_bill_s_actions_alone(conn):
    add_members(conn)
    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS))

    other = to_bill_row(BILL | {"number": "7777"})
    save(conn, other, [], to_action_rows(ACTIONS[:1]))

    assert count(conn, "bill_actions") == 5


def test_deleting_a_bill_deletes_its_actions(conn):
    add_members(conn)
    save(conn, to_bill_row(BILL), [], to_action_rows(ACTIONS))

    conn.execute("DELETE FROM bills")

    assert count(conn, "bill_actions") == 0


def test_load_saves_each_bill_with_its_actions(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi(bill=WITH_ACTIONS))

    loaded, _ = load(committing_conn)

    assert loaded == 1
    assert count(committing_conn, "bill_actions") == 4


def test_fetch_json_adds_params_to_the_address_and_keeps_the_key_out(monkeypatch):
    asked = []

    class Answer:
        def __enter__(self):
            return io.BytesIO(b'{"actions": []}')

        def __exit__(self, *exc):
            return False

    def fake_open_url(request, timeout):
        asked.append(request)
        return Answer()

    monkeypatch.setattr("ingest.bills.open_url", fake_open_url)
    monkeypatch.setenv("CONGRESS_API_KEY", "not-a-real-key")

    fetch_json("bill/119/hr/9999/actions", {"limit": 250})

    request = asked[0]
    assert request.full_url == (
        "https://api.congress.gov/v3/bill/119/hr/9999/actions?format=json&limit=250"
    )
    assert "not-a-real-key" not in request.full_url
    assert request.get_header("X-api-key") == "not-a-real-key"
