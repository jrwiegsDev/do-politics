"""Tests for the bills loader.

The records below are made up, in the layout Congress.gov really sends, so
no test calls the API and none needs a key.
"""

import urllib.error
from datetime import UTC, date, datetime, time, timedelta
from email.message import Message

import psycopg
import pytest
from helpers import add_bill, add_members, add_vote, last_run

from ingest.bills import (
    NotSettledYet,
    bills_to_download,
    catch_up_titles,
    fetch_json,
    list_bills,
    load,
    read_bill,
    read_titles,
    save,
    title_kind,
    to_action_rows,
    to_bill_row,
    to_summary_row,
    to_title_rows,
)
from ingest.fetch import Download

# The day the tests run as, in the 119th Congress.
TODAY = date(2026, 11, 17)

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


def listed(number, update_date="2026-11-16", bill_type="HR", congress=119):
    """Build one item of Congress.gov's list of a Congress's bills."""
    return {
        "congress": congress,
        "type": bill_type,
        "number": str(number),
        "title": "Example Act",
        "updateDate": update_date,
    }


# H.R. 1's titles as Congress.gov lists them, trimmed: four distinct titles
# under several types, one of them listed as both popular and short.
TITLES = [
    {"titleType": "Display Title", "title": "Example Act"},
    {"titleType": "Official Title as Introduced", "title": "To do a thing."},
    {"titleType": "Popular Titles", "title": "Big Example Act"},
    {"titleType": "Short Title(s) as Passed House", "title": "Big Example Act"},
    {
        "titleType": "Short Titles for portions of this bill from ENR (Enrolled) bill text",
        "title": "Small Part Act of 2025",
    },
]


class FakeApi:
    """Stand in for fetch_json, and remember each address that was asked for.

    The list of a Congress's bills ('bill/119') is answered from listing,
    a page at a time. Any bill asked for is answered with the made-up bill
    under that number, its summaries with the made-up summaries, and its
    actions with the made-up actions, and its titles with the made-up titles
    (or, given an exception, by raising it). The query parameters of each
    request are kept in params. Given a status, every request for a bill fails with
    that HTTP status instead.
    """

    def __init__(
        self,
        bill=BILL,
        summaries=SUMMARIES,
        actions=ACTIONS,
        status=None,
        listing=(),
        titles=TITLES,
    ):
        self.bill = bill
        self.summaries = summaries
        self.actions = actions
        self.titles = titles
        self.status = status
        self.listing = list(listing)
        self.asked = []
        self.params = []

    def __call__(self, path: str, params: dict | None = None) -> dict:
        self.asked.append(path)
        self.params.append(params)
        parts = path.split("/")
        if len(parts) == 2:
            congress = int(parts[1])
            items = [item for item in self.listing if item["congress"] == congress]
            start = (params or {}).get("offset", 0)
            end = start + (params or {}).get("limit", 20)
            return {"bills": items[start:end], "pagination": {"count": len(items)}}
        if self.status is not None:
            raise urllib.error.HTTPError(
                f"https://example.test/{path}", self.status, "Refused", Message(), None
            )
        if path.endswith("/summaries"):
            return {"summaries": self.summaries}
        if path.endswith("/actions"):
            return {"actions": self.actions}
        if path.endswith("/titles"):
            if isinstance(self.titles, Exception):
                raise self.titles
            return {"titles": self.titles}
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


def test_bill_with_no_sponsor_has_none():
    assert to_bill_row(BILL | {"sponsors": []})["sponsor_bioguide_id"] is None


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

    with pytest.raises(ValueError, match="lists 3 summaries, but Congress.gov sent 2"):
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

    loaded, missing, unknown_sponsor, _ = load(committing_conn, TODAY)

    recorded = last_run(committing_conn)
    assert (loaded, missing, unknown_sponsor) == (1, [], [])
    assert count(committing_conn, "bills") == 1
    assert count(committing_conn, "bill_summaries") == 2
    assert api.asked == [
        "bill/119",
        "bill/119/hr/9999",
        "bill/119/hr/9999/summaries",
        "bill/119/hr/9999/titles",
    ]
    assert (recorded["job"], recorded["status"]) == ("bills", "succeeded")
    assert recorded["rows_loaded"] == 1


def test_load_reports_and_skips_a_bill_that_is_not_found(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi(status=404))

    loaded, missing, _, _ = load(committing_conn, TODAY)

    assert (loaded, missing) == (0, ["H.R. 9999 (119th Congress)"])
    assert last_run(committing_conn)["status"] == "succeeded"


def test_load_fails_the_run_on_any_other_refusal(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi(status=403))

    with pytest.raises(urllib.error.HTTPError):
        load(committing_conn, TODAY)

    recorded = last_run(committing_conn)
    assert recorded["status"] == "failed"
    assert recorded["error"] == "HTTPError: HTTP Error 403: Refused"


def test_load_stops_at_the_limit(committing_conn, monkeypatch, no_waiting):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    add_vote(committing_conn, 2, 6, {"T000001": "Yea"})
    committing_conn.execute("UPDATE votes SET bill_number = 1 WHERE roll_number = 2")
    use(monkeypatch, FakeApi())

    loaded, _, _, _ = load(committing_conn, TODAY, limit=1)

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


# The Library of Congress's wordless copy of a step, as Congress.gov sent it
# for S. 3347 beside its worded 'Introduced in Senate'.
WORDLESS = {
    "actionCode": "Intro-S",
    "actionDate": "2025-01-15",
    "type": "IntroReferral",
    "sourceSystem": {"code": 9, "name": "Library of Congress"},
}


def test_action_with_no_words_is_left_out_and_the_rest_stay_numbered():
    rows = to_action_rows([*ACTIONS[:2], WORDLESS, *ACTIONS[2:]])

    assert [row["position"] for row in rows] == [0, 1, 2, 3]
    assert "Intro-S" not in [row["action_code"] for row in rows]
    assert rows == to_action_rows(ACTIONS)


def test_read_bill_counts_a_wordless_action_as_sent(monkeypatch):
    # The bill says 5 actions and Congress.gov sends 5, one of them wordless.
    # The list is whole, so it is not refused as cut short; 4 are kept.
    bill = BILL | {"actions": {"count": 5}}
    use(monkeypatch, FakeApi(bill=bill, actions=[*ACTIONS, WORDLESS]))

    _, _, actions = read_bill(119, "hr", 9999)

    assert len(actions) == 4


def test_a_bill_with_a_wordless_action_is_saved_and_the_run_succeeds(
    committing_conn, monkeypatch, no_waiting
):
    # The run of 2026-11-18 failed on eight Senate bills like this one.
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    bill = BILL | {"actions": {"count": 5}}
    use(monkeypatch, FakeApi(bill=bill, actions=[*ACTIONS, WORDLESS]))

    loaded, _, _, _ = load(committing_conn, TODAY)

    assert loaded == 1
    assert count(committing_conn, "bill_actions") == 4
    assert last_run(committing_conn)["status"] == "succeeded"


def test_load_names_each_bill_it_saves_the_way_the_site_does(
    committing_conn, monkeypatch, no_waiting, capsys
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi(bill=WITH_ACTIONS))

    load(committing_conn, TODAY)

    printed = capsys.readouterr().out
    assert printed == "H.R. 9999 (119th Congress): 2 summaries, 4 actions\n"


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

    with pytest.raises(ValueError, match="lists 5 actions, but Congress.gov sent 4"):
        read_bill(119, "hr", 9999)


# When BILL's record last changed, its updateDate.
BILL_CHANGED_AT = datetime(2026, 9, 19, 23, 7, 8, tzinfo=UTC)


def test_read_bill_leaves_a_short_action_list_for_later_if_the_bill_just_changed(
    monkeypatch,
):
    # H.R. 2785 on 2026-11-20: read 25 minutes after it changed, its record
    # counted 8 actions while its list of actions still sent the old 5.
    use(monkeypatch, FakeApi(bill=BILL | {"actions": {"count": 5}}))

    with pytest.raises(NotSettledYet, match="lists 5 actions, but Congress.gov sent 4"):
        read_bill(119, "hr", 9999, now=BILL_CHANGED_AT + timedelta(minutes=25))


def test_read_bill_leaves_a_short_summary_list_for_later_if_the_bill_just_changed(
    monkeypatch,
):
    use(monkeypatch, FakeApi(bill=BILL | {"summaries": {"count": 3}}))

    with pytest.raises(
        NotSettledYet, match="lists 3 summaries, but Congress.gov sent 2"
    ):
        read_bill(119, "hr", 9999, now=BILL_CHANGED_AT + timedelta(hours=23))


@pytest.mark.parametrize("later", [timedelta(hours=24), timedelta(days=30)])
def test_read_bill_refuses_a_short_action_list_once_the_bill_has_had_a_day(
    monkeypatch, later
):
    use(monkeypatch, FakeApi(bill=BILL | {"actions": {"count": 5}}))

    with pytest.raises(ValueError, match="lists 5 actions, but Congress.gov sent 4"):
        read_bill(119, "hr", 9999, now=BILL_CHANGED_AT + later)


def test_read_bill_saves_a_whole_list_from_a_bill_that_just_changed(monkeypatch):
    use(monkeypatch, FakeApi(bill=WITH_ACTIONS))

    _, _, actions = read_bill(
        119, "hr", 9999, now=BILL_CHANGED_AT + timedelta(minutes=1)
    )

    assert len(actions) == 4


def test_load_leaves_an_unsettled_bill_for_the_next_run_which_saves_it(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    just_changed = BILL | {"updateDate": an_hour_ago}
    use(monkeypatch, FakeApi(bill=just_changed | {"actions": {"count": 5}}))

    loaded, _, _, unsettled = load(committing_conn, TODAY)

    assert (loaded, count(committing_conn, "bills")) == (0, 0)
    assert unsettled == [
        "H.R. 9999 (119th Congress): the bill lists 5 actions, but Congress.gov sent 4"
    ]
    assert last_run(committing_conn)["status"] == "succeeded"

    # By the next run the list has caught up with the record.
    use(monkeypatch, FakeApi(bill=just_changed | {"actions": {"count": 4}}))

    loaded, _, _, unsettled = load(committing_conn, TODAY)

    assert (loaded, unsettled) == (1, [])
    assert count(committing_conn, "bill_actions") == 4


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

    loaded, _, _, _ = load(committing_conn, TODAY)

    assert loaded == 1
    assert count(committing_conn, "bill_actions") == 4


def test_fetch_json_adds_params_to_the_address_and_keeps_the_key_out(
    monkeypatch, no_waiting
):
    asked = []

    def fake_read_url(request, timeout):
        asked.append(request)
        return Download(request.full_url, b'{"actions": []}')

    monkeypatch.setattr("ingest.bills.read_url", fake_read_url)
    monkeypatch.setenv("CONGRESS_API_KEY", "not-a-real-key")

    fetch_json("bill/119/hr/9999/actions", {"limit": 250})

    request = asked[0]
    assert request.full_url == (
        "https://api.congress.gov/v3/bill/119/hr/9999/actions?format=json&limit=250"
    )
    assert "not-a-real-key" not in request.full_url
    assert request.get_header("X-api-key") == "not-a-real-key"


def test_fetch_json_pauses_after_every_answer(monkeypatch):
    paused = []

    monkeypatch.setattr(
        "ingest.bills.read_url",
        lambda request, timeout: Download(request.full_url, b'{"bills": []}'),
    )
    monkeypatch.setattr("ingest.bills.sleep", paused.append)
    monkeypatch.setenv("CONGRESS_API_KEY", "not-a-real-key")

    fetch_json("bill/119")
    fetch_json("bill/119", {"limit": 250, "offset": 250})

    assert paused == [0.25, 0.25]


def test_read_bill_asks_for_a_long_action_list_page_by_page(monkeypatch):
    many = [ACTIONS[1]] * 300

    class PagedActions(FakeApi):
        def __call__(self, path, params=None):
            answer = super().__call__(path, params)
            if path.endswith("/actions"):
                asked = params or {}
                start = asked.get("offset", 0)
                return {"actions": many[start : start + asked["limit"]]}
            return answer

    bill = BILL | {"actions": {"count": 300}, "summaries": {"count": 0}}
    api = use(monkeypatch, PagedActions(bill=bill))

    _, _, actions = read_bill(119, "hr", 9999)

    assert len(actions) == 300
    assert api.params[1:] == [{"limit": 250}, {"limit": 250, "offset": 250}]


# Which bills a run downloads.


def test_list_bills_asks_page_by_page_and_keys_each_bill_by_type_and_number(
    monkeypatch,
):
    api = use(monkeypatch, FakeApi(listing=[listed(n) for n in range(1, 601)]))

    bills = list_bills(119)

    assert len(bills) == 600
    assert bills[("hr", 1)] == date(2026, 11, 16)
    assert api.params == [
        {"limit": 250},
        {"limit": 250, "offset": 250},
        {"limit": 250, "offset": 500},
    ]


def test_list_bills_takes_the_day_of_a_date_that_has_a_time(monkeypatch):
    use(monkeypatch, FakeApi(listing=[listed(1, "2026-11-16T19:50:47Z")]))

    assert list_bills(119) == {("hr", 1): date(2026, 11, 16)}


def test_new_and_changed_bills_are_downloaded_the_most_recently_changed_first():
    changed_on = {
        ("hr", 1): date(2026, 1, 5),  # not stored yet
        ("hr", 2): date(2026, 11, 16),  # changed after its last download
        ("hr", 3): date(2026, 11, 9),  # changed the day before its last download
        ("hr", 4): date(2026, 11, 8),  # unchanged since its last download
    }
    downloaded_on = {
        ("hr", 2): date(2026, 11, 1),
        ("hr", 3): date(2026, 11, 10),
        ("hr", 4): date(2026, 11, 10),
    }

    assert bills_to_download(changed_on, downloaded_on) == [
        ("hr", 2),
        ("hr", 3),
        ("hr", 1),
    ]


def stored_and_downloaded_on(conn, day: str) -> None:
    """Store the made-up bill as if it was last downloaded on the given day."""
    add_members(conn)
    save(conn, to_bill_row(BILL), [], [])
    conn.execute("UPDATE bills SET updated_at = %s", [f"{day} 12:00Z"])
    conn.commit()


def test_load_downloads_listed_bills_that_no_vote_names(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    api = use(monkeypatch, FakeApi(listing=[listed(1), listed(2)]))

    loaded, _, _, _ = load(committing_conn, TODAY)

    assert loaded == 2
    assert {"bill/119/hr/1", "bill/119/hr/2"} <= set(api.asked)


def test_load_leaves_a_stored_bill_that_has_not_changed(
    committing_conn, monkeypatch, no_waiting
):
    stored_and_downloaded_on(committing_conn, "2026-11-10")
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    api = use(monkeypatch, FakeApi(listing=[listed(9999, "2026-11-08")]))

    loaded, _, _, _ = load(committing_conn, TODAY)

    assert loaded == 0
    assert api.asked == ["bill/119"]


def test_load_downloads_a_stored_bill_that_changed(
    committing_conn, monkeypatch, no_waiting
):
    stored_and_downloaded_on(committing_conn, "2026-11-10")
    use(monkeypatch, FakeApi(listing=[listed(9999, "2026-11-16")]))

    loaded, _, _, _ = load(committing_conn, TODAY)

    assert loaded == 1


def test_load_skips_and_reports_a_bill_whose_sponsor_is_not_known_yet(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    newcomer = BILL | {"sponsors": [{"bioguideId": "N000001"}]}
    use(monkeypatch, FakeApi(bill=newcomer, listing=[listed(1)]))

    loaded, _, unknown_sponsor, _ = load(committing_conn, TODAY)

    assert (loaded, unknown_sponsor) == (
        0,
        ["H.R. 1 (119th Congress): sponsor N000001"],
    )
    assert count(committing_conn, "bills") == 0
    assert last_run(committing_conn)["status"] == "succeeded"


def test_load_skips_a_bill_with_no_sponsor(committing_conn, monkeypatch, no_waiting):
    add_members(committing_conn)
    use(monkeypatch, FakeApi(bill=BILL | {"sponsors": []}, listing=[listed(1)]))

    loaded, _, unknown_sponsor, _ = load(committing_conn, TODAY)

    assert (loaded, unknown_sponsor) == (0, ["H.R. 1 (119th Congress): sponsor None"])


def test_load_saves_the_readable_bills_then_fails_naming_an_unreadable_one(
    committing_conn, monkeypatch, no_waiting
):
    class OneUntitled(FakeApi):
        def __call__(self, path, params=None):
            answer = super().__call__(path, params)
            if path == "bill/119/hr/1":
                del answer["bill"]["title"]
            return answer

    add_members(committing_conn)
    listing = [listed(1, "2026-11-16"), listed(2, "2026-11-15")]
    use(monkeypatch, OneUntitled(listing=listing))

    with pytest.raises(
        ValueError, match="Could not read 1 bill: H.R. 1 \\(119th Congress\\): KeyError"
    ):
        load(committing_conn, TODAY)

    assert count(committing_conn, "bills") == 1
    assert last_run(committing_conn)["status"] == "failed"


def test_a_run_that_dies_partway_records_the_bills_it_kept(
    committing_conn, monkeypatch, no_waiting
):
    # The 2026-11-18 run: 2,119 bills saved, then Congress.gov went quiet
    # partway through an answer and every retry timed out too.
    class StallsOnTheThird(FakeApi):
        def __call__(self, path, params=None):
            if path == "bill/119/hr/3/summaries":
                raise TimeoutError("The read operation timed out")
            return super().__call__(path, params)

    add_members(committing_conn)
    listing = [
        listed(1, "2026-11-16"),
        listed(2, "2026-11-15"),
        listed(3, "2026-11-14"),
    ]
    use(monkeypatch, StallsOnTheThird(listing=listing))

    with pytest.raises(TimeoutError):
        load(committing_conn, TODAY)

    recorded = last_run(committing_conn)
    assert (recorded["status"], recorded["rows_loaded"]) == ("failed", 2)
    assert count(committing_conn, "bills") == 2


def test_load_lists_the_new_congress_first_once_it_is_seated(
    committing_conn, monkeypatch, no_waiting
):
    api = use(monkeypatch, FakeApi())

    load(committing_conn, date(2027, 1, 3))

    assert api.asked == ["bill/120", "bill/119"]


def test_load_stops_at_the_limit_and_the_next_run_carries_on(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    listing = [
        listed(1, "2026-01-07"),
        listed(2, "2026-01-06"),
        listed(3, "2026-01-05"),
    ]
    use(monkeypatch, FakeApi(listing=listing))

    first, _, _, _ = load(committing_conn, TODAY, limit=2)
    second, _, _, _ = load(committing_conn, TODAY, limit=2)

    assert (first, second) == (2, 1)
    assert count(committing_conn, "bills") == 3


# Titles


@pytest.mark.parametrize(
    ("title_type", "kind"),
    [
        ("Popular Titles", "popular"),
        ("Short Title(s) as Passed House", "short"),
        ("Short Titles as Enacted for portions of this bill", "portion"),
        (
            "Short Titles for portions of this bill from ENR (Enrolled) bill text",
            "portion",
        ),
        ("Official Title as Introduced", "official"),
        ("Display Title", "display"),
        ("Override Display Title", "display"),
    ],
)
def test_each_congress_gov_title_type_has_a_kind(title_type, kind):
    assert title_kind(title_type) == kind


def test_a_title_listed_under_several_types_is_kept_once_as_the_most_telling():
    rows = to_title_rows(TITLES)

    assert rows == [
        {"title": "Example Act", "kind": "display"},
        {"title": "To do a thing.", "kind": "official"},
        {"title": "Big Example Act", "kind": "popular"},
        {"title": "Small Part Act of 2025", "kind": "portion"},
    ]


def test_read_titles_asks_for_every_title_in_one_request(monkeypatch, no_waiting):
    api = use(monkeypatch, FakeApi())

    rows = read_titles(119, "hr", 1)

    assert api.asked == ["bill/119/hr/1/titles"]
    assert api.params == [{"limit": 250}]
    assert len(rows) == 4


def stored_titles(conn) -> list[tuple[str, str]]:
    return conn.execute("SELECT title, kind FROM bill_titles ORDER BY title").fetchall()


def downloaded_last_week(conn) -> None:
    """Date every stored bill's last download to before the listing's change."""
    conn.execute("UPDATE bills SET updated_at = '2026-11-10 12:00+00'")
    conn.commit()


def test_load_saves_a_bills_titles_with_it_and_replaces_them_next_time(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi())
    load(committing_conn, TODAY)
    assert ("Big Example Act", "popular") in stored_titles(committing_conn)
    downloaded_last_week(committing_conn)

    renamed = [{"titleType": "Display Title", "title": "Renamed Act"}]
    use(monkeypatch, FakeApi(titles=renamed, listing=[listed(9999, "2026-11-17")]))
    load(committing_conn, TODAY)

    assert stored_titles(committing_conn) == [("Renamed Act", "display")]


def test_titles_that_cannot_be_read_never_cost_the_bill(
    committing_conn, monkeypatch, no_waiting, caplog
):
    add_members(committing_conn)
    add_vote(committing_conn, 1, 5, {"T000001": "Yea"})
    use(monkeypatch, FakeApi())
    load(committing_conn, TODAY)
    before = stored_titles(committing_conn)
    downloaded_last_week(committing_conn)

    use(
        monkeypatch,
        FakeApi(titles=TimeoutError("timed out"), listing=[listed(9999, "2026-11-17")]),
    )
    with caplog.at_level("WARNING", logger="ingest.bills"):
        loaded, *_ = load(committing_conn, TODAY)

    assert loaded == 1
    assert stored_titles(committing_conn) == before
    assert "titles not read (TimeoutError: timed out)" in caplog.text


def bills_without_titles(conn) -> list[int]:
    return [
        number
        for (number,) in conn.execute(
            "SELECT bill_number FROM bills b WHERE NOT EXISTS"
            " (SELECT 1 FROM bill_titles t WHERE t.bill_id = b.id)"
            " ORDER BY bill_number"
        )
    ]


def test_the_catch_up_reads_titles_for_stored_bills_most_recently_active_first(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    for number in (1, 2, 3):
        add_bill(committing_conn, number)
    committing_conn.execute(
        "UPDATE bills SET latest_action_on = %s WHERE bill_number = 2",
        [date(2026, 10, 1)],
    )
    committing_conn.commit()
    api = use(monkeypatch, FakeApi())

    done, left, missed = catch_up_titles(committing_conn, limit=2)

    assert (done, left, missed) == (2, 1, [])
    assert api.asked[0] == "bill/119/hr/2/titles"
    assert bills_without_titles(committing_conn) == [3]
    recorded = last_run(committing_conn)
    assert (recorded["job"], recorded["status"], recorded["rows_loaded"]) == (
        "bill_titles",
        "succeeded",
        2,
    )


def test_the_catch_up_skips_a_bill_whose_titles_fail_and_tries_it_next_run(
    committing_conn, monkeypatch, no_waiting
):
    add_members(committing_conn)
    add_bill(committing_conn, 1)
    committing_conn.commit()
    use(monkeypatch, FakeApi(titles=TimeoutError("timed out")))

    done, left, missed = catch_up_titles(committing_conn)

    assert (done, left) == (0, 1)
    assert missed == ["H.R. 1 (119th Congress): TimeoutError"]
    assert bills_without_titles(committing_conn) == [1]
