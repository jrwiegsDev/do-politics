"""Tests for the generated summaries. A fake client stands in for Claude, so no
test sends a request or spends anything."""

import ast
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import action, add_bill, add_members, add_vote, last_run, summary

from ingest.prompt import (
    PROMPT_VERSION,
    REDRAFT_PROMPT_VERSION,
    REDRAFT_SYSTEM_PROMPT,
    REJECTION_TASK,
    RETRY_PROMPT_VERSION,
    RETRY_SYSTEM_PROMPT,
    REVISION_PROMPT_VERSION,
    REVISION_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from ingest.summaries import (
    MAX_TOKENS,
    OverBudget,
    actual_cost,
    bill_argument,
    bills_to_write,
    build_requests,
    check_budget,
    collect,
    local_time,
    marked_changes,
    redrafts_due,
    request_params,
    review,
    revise,
    revisions_due,
    revisit,
    sample,
    store,
    submit,
    within_budget,
    worst_case_cost,
)

OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
INTRODUCED = ("00", date(2025, 1, 15), "Introduced in House", "First words.")
PASSED = ("49", date(2025, 5, 9), "Public Law", "Last words.")


def answer(
    stop_reason="end_turn",
    input_tokens=1000,
    output_tokens=500,
    plain_summary=" Plain words. ",
    impact="It does you.",
):
    """Build a made-up answer from Claude, with a thinking block before the text."""
    text = json.dumps({"plain_summary": plain_summary, "impact": impact})
    return SimpleNamespace(
        stop_reason=stop_reason,
        model=OPUS,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
    )


class FakeBatches:
    """Stands in for client.messages.batches."""

    def __init__(self, status="ended", results=()):
        self.status = status
        self.results_to_return = list(results)
        self.created = []

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id="msgbatch_test")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self.status)

    def results(self, batch_id):
        return iter(self.results_to_return)


class FakeMessages:
    """Stands in for client.messages: counts every request as the same size."""

    def __init__(self, answers=(), tokens=1000, batches=None):
        self.answers = list(answers)
        self.tokens = tokens
        self.sent = []
        self.batches = batches or FakeBatches()

    def count_tokens(self, **params):
        return SimpleNamespace(input_tokens=self.tokens)

    def create(self, **params):
        self.sent.append(params)
        return self.answers.pop(0)


def fake_client(**kwargs):
    return SimpleNamespace(messages=FakeMessages(**kwargs))


def law():
    """Build a made-up action that says the bill became law."""
    return action(
        0, date(2025, 5, 9), "Became Public Law No: 119-1.", action_type="BecameLaw"
    )


def add_bill_with(conn, *versions, number=9999, became_law=True):
    """Store a made-up bill with these CRS summary versions and return its id.

    The bill became law unless became_law is False, since only bills that a
    vote names or that became law are drafted.
    """
    add_members(conn)
    add_bill(
        conn,
        number,
        summaries=[summary(*version) for version in versions],
        actions=[law()] if became_law else [],
    )
    row = conn.execute("SELECT id FROM bills WHERE bill_number = %s", [number])
    return row.fetchone()[0]


def stored_rows(conn):
    return conn.execute(
        "SELECT bill_id, source_version_code, plain_summary, impact, model,"
        " prompt_version, input_tokens, output_tokens, cost_usd, status, reviewed_at"
        " FROM generated_summaries ORDER BY id"
    ).fetchall()


# What a run costs.


def test_worst_case_counts_every_request_as_if_it_used_all_its_tokens():
    # 4,000 input tokens at $4 and 2 x 2,000 output tokens at $20, per million.
    assert MAX_TOKENS == 2000
    assert worst_case_cost([1000, 3000], OPUS, batch=False) == pytest.approx(0.096)
    assert worst_case_cost([1000, 3000], OPUS, batch=True) == pytest.approx(0.048)
    assert worst_case_cost([1000, 3000], HAIKU, batch=False) == pytest.approx(0.0024)


def test_worst_case_of_no_requests_is_nothing():
    assert worst_case_cost([], OPUS, batch=False) == 0


def test_actual_cost_is_exact_and_halved_for_a_batch():
    usage = answer(input_tokens=1000, output_tokens=500).usage

    # 1,000 x $4 + 500 x $20, per million.
    assert actual_cost(usage, OPUS, batch=False) == Decimal("0.014")
    assert actual_cost(usage, OPUS, batch=True) == Decimal("0.007")


def test_a_run_over_budget_raises_and_says_nothing_was_sent():
    with pytest.raises(OverBudget, match="Nothing was sent"):
        check_budget([1000] * 100, OPUS, batch=False, budget=0.50)


def test_a_run_within_budget_passes(capsys):
    check_budget([1000], OPUS, batch=True, budget=0.50)

    shown = capsys.readouterr().out
    assert "The most they could cost through the Batch API: $0.02" in shown


def test_a_model_without_a_price_is_refused():
    with pytest.raises(ValueError, match="No price is known"):
        build_requests(fake_client(), [], "claude-made-up-1")


def test_a_haiku_request_over_its_price_limit_is_refused(conn):
    add_bill_with(conn, INTRODUCED)
    bills = bills_to_write(conn, HAIKU)

    with pytest.raises(ValueError, match="too long for the Haiku price"):
        build_requests(fake_client(tokens=100_001), bills, HAIKU)


# What is sent.


def test_a_request_carries_the_policy_the_summary_and_the_limits(conn):
    add_bill_with(conn, INTRODUCED)
    [bill] = bills_to_write(conn, OPUS)

    request = request_params(bill, OPUS)

    assert request["model"] == OPUS
    assert request["max_tokens"] == MAX_TOKENS
    assert request["system"] == SYSTEM_PROMPT
    assert "<editorial_policy>" in request["system"]
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["schema"]["required"] == [
        "plain_summary",
        "impact",
    ]
    message = request["messages"][0]["content"]
    assert "H.R. 9999, Example Act (119th Congress)" in message
    assert "Introduced in House, 2025-01-15" in message
    assert "<crs_summary>\nFirst words.\n</crs_summary>" in message


def test_the_newest_summary_is_the_one_written_from(conn):
    add_bill_with(conn, INTRODUCED, PASSED)

    [bill] = bills_to_write(conn, OPUS)

    assert bill["version_code"] == "49"
    assert bill["summary_text"] == "Last words."


def test_a_bill_with_no_summary_is_not_written_from(conn):
    add_bill_with(conn)

    assert bills_to_write(conn, OPUS) == []


def test_a_bill_no_vote_names_that_did_not_become_law_is_not_written_from(conn):
    add_bill_with(conn, INTRODUCED, became_law=False)

    assert bills_to_write(conn, OPUS) == []
    assert bills_to_write(conn, OPUS, new_only=True) == []


def test_a_bill_a_vote_names_is_written_from(conn):
    add_bill_with(conn, INTRODUCED, became_law=False)
    add_vote(conn, 1, 5, {"T000001": "Yea"})

    assert numbers(bills_to_write(conn, OPUS)) == [9999]


def test_a_bill_that_became_law_with_no_vote_is_written_from(conn):
    add_bill_with(conn, INTRODUCED)

    assert numbers(bills_to_write(conn, OPUS)) == [9999]


def test_passing_one_chamber_without_a_vote_is_not_enough(conn):
    add_members(conn)
    passed = action(
        0, date(2025, 3, 3), "Passed/agreed to in House: Passed without objection."
    )
    add_bill(conn, 9999, summaries=[summary(*INTRODUCED)], actions=[passed])

    assert bills_to_write(conn, OPUS) == []


def test_a_named_bill_is_written_from_with_no_vote_or_law(conn):
    add_bill_with(conn, INTRODUCED, became_law=False)

    assert numbers(bills_to_write(conn, OPUS, bills=["119/hr/9999"])) == [9999]


def test_a_bill_already_answered_by_this_prompt_and_model_is_skipped(conn):
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))

    assert bills_to_write(conn, OPUS) == []
    # Another model has not answered it yet.
    assert len(bills_to_write(conn, HAIKU)) == 1


def decide(conn, status, note=None, days_ago=0):
    """Record a decision on every stored answer, made this many days ago."""
    conn.execute(
        "UPDATE generated_summaries SET status = %s, review_note = %s,"
        " reviewed_at = now() - make_interval(days => %s)",
        [status, note, days_ago],
    )


def test_a_bill_approved_under_another_prompt_is_not_written_again(conn):
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", "older-prompt", answer(), Decimal("0.01"))
    decide(conn, "approved")

    assert bills_to_write(conn, OPUS) == []
    # Named on purpose, it is written again.
    assert len(bills_to_write(conn, OPUS, bills=["119/hr/9999"])) == 1


def test_a_bill_rejected_under_another_prompt_is_written_again(conn):
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", "older-prompt", answer(), Decimal("0.01"))
    decide(conn, "rejected", "Too vague.")

    assert len(bills_to_write(conn, OPUS)) == 1


# --new-only, which the nightly job uses: only bills with no generated
# summary of any kind.


def numbers(bills):
    return [bill["bill_number"] for bill in bills]


def test_new_only_keeps_only_bills_with_no_generated_summary(conn):
    answered = add_bill_with(conn, INTRODUCED, number=9999)
    store(conn, answered, "00", "older-prompt", answer(), Decimal("0.01"))
    add_bill_with(conn, INTRODUCED, number=8888)

    # Without it, the older prompt's draft counts as needing a new answer.
    assert numbers(bills_to_write(conn, OPUS)) == [8888, 9999]
    assert numbers(bills_to_write(conn, OPUS, new_only=True)) == [8888]


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_new_only_leaves_out_a_reviewed_bill_whatever_the_decision(conn, status):
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", "older-prompt", answer(), Decimal("0.01"))
    decide(conn, status, "A note.")

    assert bills_to_write(conn, OPUS, new_only=True) == []


@pytest.mark.parametrize("status", ["approved", "draft"])
def test_new_only_drafts_a_bill_whose_summary_moved_to_a_newer_stage(conn, status):
    # Written from Introduced; the newest CRS summary is now Public Law.
    bill_id = add_bill_with(conn, INTRODUCED, PASSED)
    store(conn, bill_id, "00", "older-prompt", answer(), Decimal("0.01"))
    if status == "approved":
        decide(conn, "approved")

    [bill] = bills_to_write(conn, OPUS, new_only=True)

    assert (bill["version_code"], bill["summary_text"]) == ("49", "Last words.")


def test_new_only_leaves_a_newer_stage_once_anything_is_written_from_it(conn):
    bill_id = add_bill_with(conn, INTRODUCED, PASSED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "approved")
    # A draft from the newest stage under another prompt, since rejected: it
    # was written and decided, so the nightly job does not write it again.
    store(conn, bill_id, "49", "older-prompt", answer(), Decimal("0.01"))
    conn.execute(
        "UPDATE generated_summaries SET status = 'rejected', reviewed_at = now()"
        " WHERE source_version_code = '49'"
    )

    assert bills_to_write(conn, OPUS, new_only=True) == []


def test_new_only_leaves_a_bill_with_only_rejections_to_revise(conn):
    # Rejected from an older stage: revise redrafts it from the newest one.
    bill_id = add_bill_with(conn, INTRODUCED, PASSED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "rejected", "Too vague.")

    assert bills_to_write(conn, OPUS, new_only=True) == []


def test_new_only_and_named_bills_must_both_match(conn):
    answered = add_bill_with(conn, INTRODUCED, number=9999)
    store(conn, answered, "00", "older-prompt", answer(), Decimal("0.01"))
    add_bill_with(conn, INTRODUCED, number=8888)
    add_bill_with(conn, INTRODUCED, number=7777)

    named = ["119/hr/9999", "119/hr/8888"]

    assert numbers(bills_to_write(conn, OPUS, bills=named, new_only=True)) == [8888]


def test_new_only_respects_the_limit(conn):
    for number in (7777, 8888, 9999):
        add_bill_with(conn, INTRODUCED, number=number)

    assert numbers(bills_to_write(conn, OPUS, limit=2, new_only=True)) == [7777, 8888]


def test_a_newer_summary_is_written_from_again(conn):
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    add_bill(
        conn, 9999, summaries=[summary(*INTRODUCED), summary(*PASSED)], actions=[law()]
    )

    [bill] = bills_to_write(conn, OPUS)

    assert bill["version_code"] == "49"


def test_the_list_can_be_narrowed_to_named_bills_and_limited(conn):
    add_bill_with(conn, INTRODUCED, number=9999)
    add_bill_with(conn, INTRODUCED, number=8888)

    named = bills_to_write(conn, OPUS, bills=["119/hr/9999"])
    limited = bills_to_write(conn, OPUS, limit=1)

    assert [bill["bill_number"] for bill in named] == [9999]
    assert len(limited) == 1


@pytest.mark.parametrize("value", ["119/hr/1", "118/sjres/42"])
def test_a_bill_is_named_as_congress_type_number(value):
    assert bill_argument(value) == value


@pytest.mark.parametrize("value", ["hr/1", "119/HR/1", "119/hr/1; DROP TABLE bills"])
def test_a_badly_named_bill_is_refused(value):
    with pytest.raises(Exception, match="is not like 119/hr/1"):
        bill_argument(value)


# One at a time.


def test_sample_stores_each_answer_as_a_draft_with_its_cost(committing_conn):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    bills = bills_to_write(conn, OPUS)
    client = fake_client(answers=[answer()])

    spent = sample(conn, client, bills, OPUS, budget=1.0)

    assert spent == Decimal("0.014")
    [row] = stored_rows(conn)
    assert row == (
        bill_id,
        "00",
        "Plain words.",
        "It does you.",
        OPUS,
        PROMPT_VERSION,
        1000,
        500,
        Decimal("0.014000"),
        "draft",
        None,
    )
    assert last_run(conn)["rows_loaded"] == 1
    assert last_run(conn)["status"] == "succeeded"


def test_sample_shows_each_answer_so_it_can_be_read_at_once(committing_conn, capsys):
    conn = committing_conn
    add_bill_with(conn, INTRODUCED)
    client = fake_client(answers=[answer()])

    sample(conn, client, bills_to_write(conn, OPUS), OPUS, budget=1.0)

    shown = capsys.readouterr().out
    assert "Plain words." in shown
    assert "How this does you:\nIt does you." in shown


def test_sample_over_budget_sends_nothing(committing_conn):
    conn = committing_conn
    add_bill_with(conn, INTRODUCED)
    client = fake_client(tokens=1_000_000)

    with pytest.raises(OverBudget):
        sample(conn, client, bills_to_write(conn, OPUS), OPUS, budget=1.0)

    assert client.messages.sent == []
    assert stored_rows(conn) == []


# Sending what fits: the nightly jobs leave what is over budget for the next run.

# The most one request of 1,000 input tokens could cost on Opus, one at a
# time: 1,000 x $4 + 2,000 x $20 per million tokens, $0.044.
ONE = 0.044


def test_requests_are_taken_in_order_while_they_fit():
    assert within_budget([1000, 1000, 1000], OPUS, budget=2.5 * ONE) == [0, 1]


def test_a_request_too_big_to_fit_is_left_and_later_ones_still_go():
    # 50,000 tokens could cost $0.24 alone, so it waits; the next one fits.
    assert within_budget([1000, 50_000, 1000], OPUS, budget=2.5 * ONE) == [0, 2]


def test_nothing_is_taken_when_nothing_fits():
    assert within_budget([50_000], OPUS, budget=ONE) == []


def test_sample_sends_what_fits_and_leaves_the_rest(committing_conn, capsys):
    conn = committing_conn
    for number in (7777, 8888, 9999):
        add_bill_with(conn, INTRODUCED, number=number)
    client = fake_client(answers=[answer(), answer()], tokens=1000)

    sample(conn, client, bills_to_write(conn, OPUS), OPUS, 2.5 * ONE, fit=True)

    assert len(client.messages.sent) == 2
    assert len(stored_rows(conn)) == 2
    shown = capsys.readouterr().out
    assert "1 left for the next run, to stay within the budget: H.R. 9999" in shown
    # The next run takes the one left.
    assert numbers(bills_to_write(conn, OPUS, new_only=True)) == [9999]


def test_sample_that_fits_entirely_leaves_nothing(committing_conn, capsys):
    conn = committing_conn
    add_bill_with(conn, INTRODUCED)
    client = fake_client(answers=[answer()], tokens=1000)

    sample(conn, client, bills_to_write(conn, OPUS), OPUS, budget=1.0, fit=True)

    assert len(client.messages.sent) == 1
    assert "left for the next run" not in capsys.readouterr().out


def test_revise_sends_what_fits_and_leaves_the_rest(committing_conn, capsys):
    conn = committing_conn
    for number in (7777, 8888, 9999):
        bill_id = add_bill_with(conn, INTRODUCED, number=number)
        store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "rejected", "Too vague.")
    conn.commit()
    client = fake_client(answers=[answer()], tokens=1000)

    revise(conn, client, redrafts_due(conn), OPUS, budget=1.5 * ONE, fit=True)

    assert len(client.messages.sent) == 1
    assert len(redrafts_due(conn)) == 2
    assert "2 left for the next run" in capsys.readouterr().out


def test_the_nightly_summary_jobs_send_what_fits():
    blueprint = Path(__file__).resolve().parent.parent / "render.yaml"
    commands = [
        line.strip()
        for line in blueprint.read_text().splitlines()
        if "startCommand:" in line and "ingest.summaries" in line
    ]

    assert len(commands) == 2  # draft-new-summaries and revise-summaries
    for command in commands:
        assert "--budget " in command
        assert command.endswith("--fit-budget")


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_an_answer_cut_short_or_declined_is_paid_for_but_not_stored(
    committing_conn, stop_reason
):
    conn = committing_conn
    add_bill_with(conn, INTRODUCED)
    client = fake_client(answers=[answer(stop_reason=stop_reason)])

    spent = sample(conn, client, bills_to_write(conn, OPUS), OPUS, budget=1.0)

    assert spent == Decimal("0.014")
    assert stored_rows(conn) == []


# Through the Batch API.


def test_submit_sends_one_batch_with_ids_that_name_the_bill_summary_and_prompt(conn):
    bill_id = add_bill_with(conn, INTRODUCED)
    bills = bills_to_write(conn, OPUS)
    client = fake_client()

    batch_id = submit(client, bills, OPUS, budget=1.0)

    assert batch_id == "msgbatch_test"
    [requests] = client.messages.batches.created
    assert requests == [
        {
            "custom_id": f"b{bill_id}-v00-p{PROMPT_VERSION}",
            "params": request_params(bills[0], OPUS),
        }
    ]


def test_submit_over_budget_sends_nothing(conn):
    add_bill_with(conn, INTRODUCED)
    client = fake_client(tokens=1_000_000)

    with pytest.raises(OverBudget):
        submit(client, bills_to_write(conn, OPUS), OPUS, budget=1.0)

    assert client.messages.batches.created == []


def test_collect_waits_for_a_batch_still_running(conn):
    client = fake_client(batches=FakeBatches(status="in_progress"))

    assert collect(conn, client, "msgbatch_test") is None
    assert stored_rows(conn) == []


def test_collect_stores_answers_at_half_price_once_and_skips_failures(
    committing_conn,
):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    results = [
        SimpleNamespace(
            custom_id=f"b{bill_id}-v00-p{PROMPT_VERSION}",
            result=SimpleNamespace(type="succeeded", message=answer()),
        ),
        SimpleNamespace(
            custom_id=f"b{bill_id}-v49-p{PROMPT_VERSION}",
            result=SimpleNamespace(type="errored"),
        ),
        SimpleNamespace(
            custom_id="something-else",
            result=SimpleNamespace(type="succeeded", message=answer()),
        ),
    ]
    client = fake_client(batches=FakeBatches(results=results))

    spent = collect(conn, client, "msgbatch_test")
    collect(conn, client, "msgbatch_test")

    assert spent == Decimal("0.007")
    [row] = stored_rows(conn)
    assert row[1] == "00"
    assert row[8] == Decimal("0.007000")


# Review.


def test_review_records_each_decision_and_its_note(committing_conn, capsys):
    conn = committing_conn
    first = add_bill_with(conn, INTRODUCED, number=8888)
    second = add_bill_with(conn, INTRODUCED, number=9999)
    store(conn, first, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    store(conn, second, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    conn.commit()
    replies = iter(["a", "", "r", "Too vague."])

    decided = review(conn, ask=lambda prompt: next(replies))

    assert decided == 2
    rows = conn.execute(
        "SELECT status, review_note, reviewed_at IS NOT NULL"
        " FROM generated_summaries ORDER BY bill_id"
    ).fetchall()
    assert rows == [("approved", None, True), ("rejected", "Too vague.", True)]
    shown = capsys.readouterr().out
    assert "H.R. 8888: Example Act" in shown
    assert "First words." in shown
    assert "Plain words." in shown


@pytest.mark.parametrize("reply", ["s", "q"])
def test_skipping_or_quitting_leaves_a_draft_as_it_is(committing_conn, reply):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    conn.commit()

    decided = review(conn, ask=lambda prompt: reply)

    assert decided == 0
    assert stored_rows(conn)[0][9] == "draft"


def test_undo_sends_a_recent_decision_back_to_the_drafts_with_its_note(
    committing_conn, capsys
):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "approved", "Approved by mistake.", days_ago=6)
    conn.commit()

    reopened = revisit(conn, ask=lambda prompt: "u")

    assert reopened == 1
    [row] = stored_rows(conn)
    assert (row[9], row[10]) == ("draft", None)
    assert conn.execute("SELECT review_note FROM generated_summaries").fetchone() == (
        "Approved by mistake.",
    )
    shown = capsys.readouterr().out
    assert "H.R. 9999: Example Act" in shown
    assert "Note: Approved by mistake." in shown


def test_a_decision_is_shown_in_local_time(committing_conn, capsys):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "approved")
    conn.commit()
    [reviewed_at] = conn.execute(
        "SELECT reviewed_at FROM generated_summaries"
    ).fetchone()

    revisit(conn, ask=lambda prompt: "s")

    assert f"Approved {local_time(reviewed_at)}" in capsys.readouterr().out


def test_local_time_converts_from_utc():
    approved_utc = datetime(2026, 10, 19, 2, 7, tzinfo=UTC)

    assert local_time(approved_utc) == f"{approved_utc.astimezone():%Y-%m-%d %H:%M}"
    assert local_time(approved_utc).startswith("2026-10-")


@pytest.mark.parametrize("reply", ["s", "q"])
def test_skipping_or_quitting_keeps_a_decision(committing_conn, reply):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "rejected")
    conn.commit()

    assert revisit(conn, ask=lambda prompt: reply) == 0
    assert stored_rows(conn)[0][9] == "rejected"


def test_a_decision_older_than_the_undo_window_is_not_offered(committing_conn, capsys):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "approved", days_ago=8)
    conn.commit()

    assert revisit(conn, ask=lambda prompt: "u") == 0
    assert stored_rows(conn)[0][9] == "approved"
    assert "No decisions in the last 7 days." in capsys.readouterr().out


def test_a_reopened_draft_keeps_its_earlier_note_when_none_is_given(
    committing_conn, capsys
):
    conn = committing_conn
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    conn.execute("UPDATE generated_summaries SET review_note = 'Too vague.'")
    conn.commit()
    replies = iter(["r", ""])

    review(conn, ask=lambda prompt: next(replies))

    assert conn.execute(
        "SELECT status, review_note FROM generated_summaries"
    ).fetchone() == ("rejected", "Too vague.")
    assert "Earlier note: Too vague." in capsys.readouterr().out


# Revisions: an approval with a note asks for a change.


def approved_with_note(conn, note="Say sex assigned at birth."):
    """Store an answer, approve it with this note, and return its id."""
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "approved", note)
    conn.commit()
    return conn.execute("SELECT id FROM generated_summaries").fetchone()[0]


def revised(**text):
    """Build a revision's answer: the approved text with one change."""
    return answer(
        plain_summary=text.get("plain_summary", "Plain words."),
        impact=text.get("impact", "It does you."),
    )


def test_only_an_approval_with_a_note_is_due_for_a_revision(conn):
    bill_id = add_bill_with(conn, INTRODUCED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    decide(conn, "approved")
    assert revisions_due(conn) == []

    decide(conn, "rejected", "Too vague.")
    assert revisions_due(conn) == []

    decide(conn, "approved", "Say sex assigned at birth.")
    [row] = revisions_due(conn)
    assert row["review_note"] == "Say sex assigned at birth."
    assert row["summary_text"] == "First words."


def test_a_revision_request_carries_the_approved_text_and_the_note(committing_conn):
    conn = committing_conn
    approved_with_note(conn)
    [row] = revisions_due(conn)

    client = fake_client(answers=[revised(impact="It does you more.")])
    revise(conn, client, [row], OPUS, budget=1.0)

    [request] = client.messages.sent
    assert request["system"] == REVISION_SYSTEM_PROMPT
    message = request["messages"][0]["content"]
    assert "<crs_summary>\nFirst words.\n</crs_summary>" in message
    assert '"plain_summary": "Plain words."' in message
    assert "<editor_note>\nSay sex assigned at birth.\n</editor_note>" in message


def test_a_revision_is_stored_as_a_draft_that_points_back(committing_conn, capsys):
    conn = committing_conn
    original = approved_with_note(conn)
    client = fake_client(answers=[revised(impact="It does you more.")])

    revise(conn, client, revisions_due(conn), OPUS, budget=1.0)

    row = conn.execute(
        "SELECT status, prompt_version, revised_from, impact FROM generated_summaries"
        " WHERE revised_from IS NOT NULL"
    ).fetchone()
    assert row == ("draft", REVISION_PROMPT_VERSION, original, "It does you more.")
    assert "It does [-you.-] {+you more.+}" in capsys.readouterr().out


def test_a_row_is_revised_once_whatever_happens_to_the_revision(committing_conn):
    conn = committing_conn
    approved_with_note(conn)
    revise(conn, fake_client(answers=[revised()]), revisions_due(conn), OPUS, 1.0)
    conn.execute(
        "UPDATE generated_summaries SET status = 'rejected', reviewed_at = now()"
        " WHERE revised_from IS NOT NULL"
    )

    assert revisions_due(conn) == []


def test_the_database_refuses_a_second_revision_of_one_row(committing_conn):
    conn = committing_conn
    original = approved_with_note(conn)
    bill_id = conn.execute("SELECT bill_id FROM generated_summaries").fetchone()[0]

    first = store(conn, bill_id, "00", "first", revised(), Decimal("0.01"), original)
    second = store(conn, bill_id, "00", "second", revised(), Decimal("0.01"), original)

    assert (first, second) == (True, False)


def test_an_approved_revision_with_a_note_is_due_for_its_own_revision(committing_conn):
    conn = committing_conn
    approved_with_note(conn)
    revise(conn, fake_client(answers=[revised()]), revisions_due(conn), OPUS, 1.0)
    conn.execute(
        "UPDATE generated_summaries SET status = 'approved', reviewed_at = now(),"
        " review_note = 'Shorter, please.' WHERE revised_from IS NOT NULL"
    )

    [row] = revisions_due(conn)
    assert row["review_note"] == "Shorter, please."
    # A revision of a revision shares the revision prompt and is still stored.
    client = fake_client(answers=[revised(plain_summary="Plain.")])
    revise(conn, client, [row], OPUS, budget=1.0)
    assert len(stored_rows(conn)) == 3


def test_marked_changes_shows_only_what_changed():
    old = "Federal money could no longer pay for it."
    new = "Federal Medicaid money could no longer pay for it, even with consent."

    assert marked_changes(old, old) == old
    assert marked_changes(old, new) == (
        "Federal {+Medicaid+} money could no longer pay for "
        "[-it.-] {+it, even with consent.+}"
    )


def test_review_shows_revisions_first_with_the_note_and_the_changes(
    committing_conn, capsys
):
    conn = committing_conn
    original = approved_with_note(conn)
    other = add_bill_with(conn, INTRODUCED, number=1111)
    store(conn, other, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    conn.commit()
    client = fake_client(answers=[revised(impact="It does you more.")])
    revise(conn, client, revisions_due(conn), OPUS, budget=1.0)
    capsys.readouterr()
    replies = iter(["a", "", "q"])

    review(conn, ask=lambda prompt: next(replies))

    shown = capsys.readouterr().out
    assert shown.index("H.R. 9999") < shown.index("H.R. 1111")
    assert 'with note: "Say sex assigned at birth."' in shown
    assert "It does [-you.-] {+you more.+}" in shown
    statuses = conn.execute(
        "SELECT id = %s, status FROM generated_summaries"
        " WHERE bill_id <> %s ORDER BY id",
        [original, other],
    ).fetchall()
    # The original stays approved; the approved revision replaces it on the site.
    assert statuses == [(True, "approved"), (False, "approved")]


# Rewrites after a rejection: a redraft, or another try at a revision.


def set_decision(conn, row_id, status, note=None, days_ago=0):
    """Record a decision on one stored answer, made this many days ago."""
    conn.execute(
        "UPDATE generated_summaries SET status = %s, review_note = %s,"
        " reviewed_at = now() - make_interval(days => %s) WHERE id = %s",
        [status, note, days_ago, row_id],
    )


def newest_id(conn):
    return conn.execute("SELECT max(id) FROM generated_summaries").fetchone()[0]


def rejected_draft(conn, note="Too vague."):
    """Store an answer from the older CRS summary, reject it, and return its id."""
    bill_id = add_bill_with(conn, INTRODUCED, PASSED)
    store(conn, bill_id, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    rejected = newest_id(conn)
    set_decision(conn, rejected, "rejected", note)
    conn.commit()
    return rejected


def rejected_revision(conn, note="Name no states."):
    """Approve an answer with a note, revise it, reject the revision; return its id."""
    approved_with_note(conn)
    revise(
        conn,
        fake_client(answers=[revised(impact="Texas.")]),
        revisions_due(conn),
        OPUS,
        1.0,
    )
    revision = newest_id(conn)
    set_decision(conn, revision, "rejected", note)
    conn.commit()
    return revision


def test_a_rewrite_after_a_rejection_has_its_own_instructions_and_version():
    assert REDRAFT_SYSTEM_PROMPT == f"{SYSTEM_PROMPT}\n\n{REJECTION_TASK}"
    assert RETRY_SYSTEM_PROMPT == f"{REVISION_SYSTEM_PROMPT}\n\n{REJECTION_TASK}"
    versions = {
        PROMPT_VERSION,
        REVISION_PROMPT_VERSION,
        REDRAFT_PROMPT_VERSION,
        RETRY_PROMPT_VERSION,
    }
    assert len(versions) == 4


@pytest.mark.parametrize("note", ["Too vague.", None])
def test_a_rejected_draft_is_due_for_a_redraft_from_the_newest_summary(
    committing_conn, note
):
    conn = committing_conn
    rejected = rejected_draft(conn, note)

    [row] = redrafts_due(conn)

    assert row["kind"] == "redraft"
    assert row["revised_from"] == rejected
    assert (row["version_code"], row["summary_text"]) == ("49", "Last words.")
    assert row["rejected_summary"] == "Plain words."
    assert row["rejection_note"] == note
    assert revisions_due(conn) == []


@pytest.mark.parametrize("status", ["approved", "draft"])
def test_a_rejection_replaced_by_an_approval_or_a_draft_is_not_redrafted(
    committing_conn, status
):
    conn = committing_conn
    rejected_draft(conn)
    bill_id = conn.execute("SELECT bill_id FROM generated_summaries").fetchone()[0]
    store(conn, bill_id, "49", "newer-prompt", answer(), Decimal("0.01"))
    if status == "approved":
        set_decision(conn, newest_id(conn), "approved")

    assert redrafts_due(conn) == []


def test_only_the_newest_rejection_of_a_bill_is_redrafted(committing_conn):
    conn = committing_conn
    first = rejected_draft(conn, "First note.")
    set_decision(conn, first, "rejected", "First note.", days_ago=1)
    bill_id = conn.execute("SELECT bill_id FROM generated_summaries").fetchone()[0]
    store(conn, bill_id, "49", "newer-prompt", answer(), Decimal("0.01"))
    set_decision(conn, newest_id(conn), "rejected", "Second note.")

    [row] = redrafts_due(conn)
    assert row["rejection_note"] == "Second note."


def test_a_redraft_request_carries_the_rejected_text_and_the_note(committing_conn):
    conn = committing_conn
    rejected_draft(conn)
    client = fake_client(answers=[answer(plain_summary="Redrafted.")])

    revise(conn, client, redrafts_due(conn), OPUS, budget=1.0)

    [request] = client.messages.sent
    assert request["system"] == REDRAFT_SYSTEM_PROMPT
    message = request["messages"][0]["content"]
    assert "<crs_summary>\nLast words.\n</crs_summary>" in message
    assert '<rejected_text>\n{\n  "plain_summary": "Plain words."' in message
    assert "<rejection_note>\nToo vague.\n</rejection_note>" in message
    assert "<approved_text>" not in message


def test_a_redraft_with_no_note_has_no_note_tag(committing_conn):
    conn = committing_conn
    rejected_draft(conn, note=None)
    client = fake_client(answers=[answer()])

    revise(conn, client, redrafts_due(conn), OPUS, budget=1.0)

    message = client.messages.sent[0]["messages"][0]["content"]
    assert "<rejected_text>" in message
    assert "<rejection_note>" not in message


def test_a_redraft_is_stored_as_a_draft_that_points_back(committing_conn, capsys):
    conn = committing_conn
    rejected = rejected_draft(conn)
    client = fake_client(answers=[answer(plain_summary="Redrafted.")])

    revise(conn, client, redrafts_due(conn), OPUS, budget=1.0)

    row = conn.execute(
        "SELECT status, prompt_version, revised_from, source_version_code,"
        " plain_summary FROM generated_summaries WHERE revised_from IS NOT NULL"
    ).fetchone()
    assert row == ("draft", REDRAFT_PROMPT_VERSION, rejected, "49", "Redrafted.")
    shown = capsys.readouterr().out
    assert "H.R. 9999, redraft:" in shown
    assert "Redrafted." in shown


def test_a_rejection_is_redrafted_once_and_a_rejected_redraft_again(committing_conn):
    conn = committing_conn
    rejected_draft(conn)
    revise(conn, fake_client(answers=[answer()]), redrafts_due(conn), OPUS, 1.0)
    # The redraft is waiting for review, so nothing is due.
    assert redrafts_due(conn) == []

    redraft = newest_id(conn)
    set_decision(conn, redraft, "rejected")
    [row] = redrafts_due(conn)
    assert row["revised_from"] == redraft

    # The second redraft shares the bill, CRS version, prompt and model with
    # the first, and is stored because it points back to a different row.
    revise(conn, fake_client(answers=[answer()]), [row], OPUS, 1.0)
    assert len(stored_rows(conn)) == 3


def test_a_rejected_revision_with_a_note_is_due_for_another_try(committing_conn):
    conn = committing_conn
    revision = rejected_revision(conn)

    [row] = revisions_due(conn)

    assert row["kind"] == "retry"
    assert row["revised_from"] == revision
    assert (row["plain_summary"], row["impact"]) == ("Plain words.", "It does you.")
    assert row["review_note"] == "Say sex assigned at birth."
    assert (row["rejected_impact"], row["rejection_note"]) == (
        "Texas.",
        "Name no states.",
    )
    # The approved text stands for the bill, so it is not redrafted.
    assert redrafts_due(conn) == []


def test_a_rejected_revision_with_no_note_keeps_the_approved_text(committing_conn):
    conn = committing_conn
    rejected_revision(conn, note=None)

    assert revisions_due(conn) == []
    assert redrafts_due(conn) == []


def test_a_rejected_revision_older_than_the_live_approved_text_is_left_alone(
    committing_conn,
):
    conn = committing_conn
    rejected_revision(conn)
    bill_id = conn.execute("SELECT bill_id FROM generated_summaries").fetchone()[0]
    # A newer draft was approved after the revision was rejected, so the
    # rejected revision was not a try at the text that is live now.
    store(conn, bill_id, "00", "newer-prompt", answer(), Decimal("0.01"))
    newer = newest_id(conn)
    set_decision(conn, newer, "approved", "Shorter, please.")

    [row] = revisions_due(conn)
    assert (row["kind"], row["revised_from"]) == ("revision", newer)


def test_no_other_try_while_a_draft_is_waiting(committing_conn):
    conn = committing_conn
    rejected_revision(conn)
    bill_id = conn.execute("SELECT bill_id FROM generated_summaries").fetchone()[0]
    store(conn, bill_id, "00", "newer-prompt", answer(), Decimal("0.01"))

    assert revisions_due(conn) == []


def test_another_try_carries_both_notes_and_points_back(committing_conn, capsys):
    conn = committing_conn
    revision = rejected_revision(conn)
    capsys.readouterr()
    client = fake_client(answers=[revised(impact="It does you more.")])

    revise(conn, client, revisions_due(conn), OPUS, budget=1.0)

    [request] = client.messages.sent
    assert request["system"] == RETRY_SYSTEM_PROMPT
    message = request["messages"][0]["content"]
    assert '<approved_text>\n{\n  "plain_summary": "Plain words."' in message
    assert "<editor_note>\nSay sex assigned at birth.\n</editor_note>" in message
    assert '"impact": "Texas."\n}\n</rejected_text>' in message
    assert "<rejection_note>\nName no states.\n</rejection_note>" in message
    row = conn.execute(
        "SELECT status, prompt_version FROM generated_summaries WHERE revised_from = %s",
        [revision],
    ).fetchone()
    assert row == ("draft", RETRY_PROMPT_VERSION)
    # Changes are marked against the approved text, not the rejected try.
    assert "It does [-you.-] {+you more.+}" in capsys.readouterr().out


def test_one_run_writes_each_kind_under_its_own_instructions(committing_conn):
    conn = committing_conn
    approved_with_note(conn)
    other = add_bill_with(conn, INTRODUCED, number=1111)
    store(conn, other, "00", PROMPT_VERSION, answer(), Decimal("0.01"))
    set_decision(conn, newest_id(conn), "rejected", "Too vague.")
    conn.commit()
    rows = revisions_due(conn) + redrafts_due(conn)
    client = fake_client(answers=[revised(), answer()])

    revise(conn, client, rows, OPUS, budget=1.0)

    assert [request["system"] for request in client.messages.sent] == [
        REVISION_SYSTEM_PROMPT,
        REDRAFT_SYSTEM_PROMPT,
    ]
    versions = conn.execute(
        "SELECT prompt_version FROM generated_summaries"
        " WHERE revised_from IS NOT NULL ORDER BY id"
    ).fetchall()
    assert versions == [(REVISION_PROMPT_VERSION,), (REDRAFT_PROMPT_VERSION,)]


def test_review_shows_a_redraft_in_full_with_the_note_on_the_rejection(
    committing_conn, capsys
):
    conn = committing_conn
    rejected_draft(conn)
    client = fake_client(answers=[answer(plain_summary="Redrafted.")])
    revise(conn, client, redrafts_due(conn), OPUS, budget=1.0)
    capsys.readouterr()

    asked = []
    review(conn, ask=lambda prompt: asked.append(prompt) or "q")

    shown = capsys.readouterr().out
    assert "Redraft. The last draft was rejected" in shown
    assert 'with note: "Too vague."' in shown
    assert "Redrafted." in shown
    assert "[r]eject (it is redrafted again)" in asked[0]


def test_review_shows_another_try_with_both_notes_and_the_changes(
    committing_conn, capsys
):
    conn = committing_conn
    rejected_revision(conn)
    client = fake_client(answers=[revised(impact="It does you more.")])
    revise(conn, client, revisions_due(conn), OPUS, budget=1.0)
    capsys.readouterr()

    review(conn, ask=lambda prompt: "q")

    shown = capsys.readouterr().out
    assert 'The note on the approved text: "Say sex assigned at birth."' in shown
    assert 'with note: "Name no states."' in shown
    assert "It does [-you.-] {+you more.+}" in shown


# Where the API can be reached from.


def test_the_web_app_never_imports_the_anthropic_sdk():
    app = Path(__file__).resolve().parent.parent / "app"
    imported = set()
    for path in app.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

    assert "fastapi" in imported  # the scan did read the app's imports
    assert "anthropic" not in imported
