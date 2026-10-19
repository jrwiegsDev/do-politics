"""Write plain-language text for bills from their official CRS summaries, with Claude.

This spends money from the monthly API credit. Nothing here runs from the web
app: every request is sent by one of these commands, run by hand. The key is
read from ANTHROPIC_API_KEY.

Usage:
    uv run --env-file .env python -m ingest.summaries estimate --limit 10
    uv run --env-file .env python -m ingest.summaries sample --bill 119/hr/1
    uv run --env-file .env python -m ingest.summaries submit --budget 6
    uv run --env-file .env python -m ingest.summaries collect msgbatch_...
    uv run --env-file .env python -m ingest.summaries revise
    uv run --env-file .env python -m ingest.summaries review --previous

estimate counts tokens, which is free, and prints the most the requests could
cost. sample sends requests one at a time, for tuning the prompt and
comparing models. submit sends them through the Batch API at half price, and
collect stores the answers once the batch has ended. sample and submit refuse
to send anything when the most it could cost is over --budget dollars.

A bill is written up from its newest CRS summary, once per prompt version and
model. Each answer is stored as a draft. review shows the drafts one at a
time beside the summary they came from, to approve or reject. Only approved
text is ever shown on the site.

An approval with no note means the text is final. An approval with a note
means the text is live, but the note asks for a change: revise rewrites it,
changing only what the note asks, and the next review shows that revision
first with its changes marked. review --previous first offers to undo any
decision from the last UNDO_DAYS days.
"""

import argparse
import difflib
import json
import re
from decimal import Decimal

import anthropic
from psycopg.rows import dict_row

from app.bills import bill_label
from ingest.db import connect
from ingest.prompt import (
    PROMPT_VERSION,
    REVISION_PROMPT_VERSION,
    REVISION_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from ingest.runs import record_run

JOB = "generated_summaries"
SOURCE = "anthropic"

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BUDGET = 2.0

# The most one answer may run to, thinking included. A summary and a callout
# take a few hundred tokens. An answer cut off at this limit is not stored.
MAX_TOKENS = 2000
EFFORT = "low"
BATCH_DISCOUNT = 0.5

# Dollars per million tokens, (input, output), from Anthropic's price list on
# 2026-10-14. Thinking is billed as output.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Haiku 5.5 costs more above this many input tokens, which PRICES does not cover.
HAIKU = "claude-haiku-5-5"
HAIKU_PRICE_LIMIT = 100_000

# The answer is JSON with exactly these two texts, so each lands in its own column.
OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "plain_summary": {"type": "string"},
            "impact": {"type": "string"},
        },
        "required": ["plain_summary", "impact"],
        "additionalProperties": False,
    },
}

# 119/hr/1: Congress, bill type and number, as in the bill tile's address.
BILL_ARGUMENT = re.compile(r"\d+/[a-z]+/\d+")

# A batch answer comes back with only the ID it was sent with, so the ID
# carries what is needed to store it: b<bill id>-v<summary version>-p<prompt version>.
CUSTOM_ID = re.compile(r"b(\d+)-v([A-Za-z0-9]+)-p([0-9a-f]+)")

BILLS_TO_WRITE_SQL = """
SELECT b.id AS bill_id, b.congress, b.bill_type, b.bill_number, b.title,
       s.version_code, s.action_desc, s.action_on, s.summary_text
FROM bills b
JOIN LATERAL (
    SELECT version_code, action_desc, action_on, summary_text
    FROM bill_summaries
    WHERE bill_id = b.id
    ORDER BY action_on DESC, version_code DESC
    LIMIT 1
) s ON true
WHERE (%(bills)s::text[] IS NULL
       OR concat_ws('/', b.congress, b.bill_type, b.bill_number) = ANY(%(bills)s))
  AND NOT EXISTS (
      SELECT 1
      FROM generated_summaries g
      WHERE g.bill_id = b.id
        AND g.source = 'crs_summary'
        AND g.source_version_code = s.version_code
        AND g.prompt_version = %(prompt_version)s
        AND g.model = %(model)s
  )
  -- A bill already approved for this CRS version is done, whatever prompt
  -- wrote it, unless it was named on purpose.
  AND (%(bills)s::text[] IS NOT NULL OR NOT EXISTS (
      SELECT 1
      FROM generated_summaries a
      WHERE a.bill_id = b.id
        AND a.source = 'crs_summary'
        AND a.source_version_code = s.version_code
        AND a.status = 'approved'
  ))
ORDER BY b.congress, b.bill_type, b.bill_number
LIMIT %(limit)s;
"""

# With no conflict target, DO NOTHING covers both unique indexes: one answer
# per bill, version, prompt and model, and one revision per row.
INSERT_SQL = """
INSERT INTO generated_summaries (
    bill_id, source, source_version_code, plain_summary, impact,
    model, prompt_version, input_tokens, output_tokens, cost_usd, revised_from
)
VALUES (
    %(bill_id)s, 'crs_summary', %(source_version_code)s, %(plain_summary)s, %(impact)s,
    %(model)s, %(prompt_version)s, %(input_tokens)s, %(output_tokens)s, %(cost_usd)s,
    %(revised_from)s
)
ON CONFLICT DO NOTHING;
"""

# An approved row with a note is live as it is, and the note says what to
# change. It is due for a revision until it has one, whatever that revision's
# fate, so a rejected revision is not tried again every night.
REVISIONS_DUE_SQL = """
SELECT p.id AS revised_from, p.bill_id, p.source_version_code AS version_code,
       p.plain_summary, p.impact, p.review_note,
       b.congress, b.bill_type, b.bill_number, b.title,
       s.action_desc, s.action_on, s.summary_text
FROM generated_summaries p
JOIN bills b ON b.id = p.bill_id
JOIN bill_summaries s
  ON s.bill_id = p.bill_id
 AND s.version_code = p.source_version_code
WHERE p.status = 'approved'
  AND p.review_note IS NOT NULL
  AND p.source = 'crs_summary'
  AND NOT EXISTS (
      SELECT 1 FROM generated_summaries c WHERE c.revised_from = p.id
  )
ORDER BY p.reviewed_at
LIMIT %(limit)s;
"""

# Revisions come first, each with the approved text and note it answers.
DRAFTS_SQL = """
SELECT g.id, g.plain_summary, g.impact, g.model, g.prompt_version, g.cost_usd,
       g.review_note, g.revised_from,
       p.plain_summary AS approved_summary, p.impact AS approved_impact,
       p.review_note AS approved_note, p.reviewed_at AS approved_at,
       b.congress, b.bill_type, b.bill_number, b.title, b.source_url,
       s.action_desc, s.action_on, s.summary_text
FROM generated_summaries g
JOIN bills b ON b.id = g.bill_id
JOIN bill_summaries s
  ON s.bill_id = g.bill_id
 AND s.version_code = g.source_version_code
LEFT JOIN generated_summaries p ON p.id = g.revised_from
WHERE g.status = 'draft'
  AND g.source = 'crs_summary'
ORDER BY g.revised_from IS NULL, b.congress, b.bill_type, b.bill_number,
         g.generated_at;
"""

# Keeps the earlier note when no new one is given, so a reopened draft's note
# survives being decided again.
REVIEW_SQL = """
UPDATE generated_summaries
SET status = %(status)s,
    review_note = coalesce(%(note)s, review_note),
    reviewed_at = now()
WHERE id = %(id)s
  AND status = 'draft';
"""

# How long a decision can still be undone.
UNDO_DAYS = 7

RECENT_DECISIONS_SQL = """
SELECT g.id, g.status, g.review_note, g.reviewed_at,
       b.bill_type, b.bill_number, b.title
FROM generated_summaries g
JOIN bills b ON b.id = g.bill_id
WHERE g.status <> 'draft'
  AND g.source = 'crs_summary'
  AND g.reviewed_at > now() - make_interval(days => %(days)s)
ORDER BY g.reviewed_at DESC;
"""

REOPEN_SQL = """
UPDATE generated_summaries
SET status = 'draft',
    reviewed_at = NULL
WHERE id = %(id)s
  AND status <> 'draft'
  AND reviewed_at > now() - make_interval(days => %(days)s);
"""


class OverBudget(Exception):
    """The requests could cost more than the budget allows, so none were sent."""


def bills_to_write(
    conn, model: str, limit: int | None = None, bills: list[str] | None = None
) -> list[dict]:
    """Return the bills whose newest CRS summary has no answer yet from this prompt and model.

    A bill with an approved summary of that CRS version is left out. bills
    narrows the list to those named, such as ['119/hr/1'], and a bill named
    there is returned even if approved, so it can be rewritten on purpose.
    """
    params = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "limit": limit,
        "bills": bills,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(BILLS_TO_WRITE_SQL, params).fetchall()


def user_message(bill: dict) -> str:
    """Return what Claude is given for one bill: its name, the summary's stage, the summary."""
    label = bill_label(bill["bill_type"], bill["bill_number"])
    return (
        f"Bill: {label}, {bill['title']} ({bill['congress']}th Congress)\n"
        f"Summary stage: {bill['action_desc']}, {bill['action_on'].isoformat()}\n"
        f"\n<crs_summary>\n{bill['summary_text']}\n</crs_summary>"
    )


def request_params(bill: dict, model: str) -> dict:
    """Return the request for one bill. A single call and a batch send the same one."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_message(bill)}],
        "output_config": {"effort": EFFORT, "format": OUTPUT_FORMAT},
    }


def count_tokens(client, params: dict) -> int:
    """Return how many input tokens a request holds. Counting is free."""
    counted = client.messages.count_tokens(
        model=params["model"],
        system=params["system"],
        messages=params["messages"],
        output_config=params["output_config"],
    )
    return counted.input_tokens


def worst_case_cost(input_tokens: list[int], model: str, batch: bool) -> float:
    """Return the most these requests could cost, in dollars.

    Each request is counted as if Claude wrote every token it is allowed,
    MAX_TOKENS, so the real cost is always lower.
    """
    input_price, output_price = PRICES[model]
    total_input = sum(input_tokens)
    total_output = len(input_tokens) * MAX_TOKENS
    dollars = (total_input * input_price + total_output * output_price) / 1_000_000
    return dollars * BATCH_DISCOUNT if batch else dollars


def build_requests(
    client, bills: list[dict], model: str
) -> tuple[list[dict], list[int]]:
    """Return the request for each bill and how many input tokens each holds."""
    if model not in PRICES:
        raise ValueError(f"No price is known for {model}.")

    requests = [request_params(bill, model) for bill in bills]
    return requests, measure(client, requests, model)


def measure(client, requests: list[dict], model: str) -> list[int]:
    """Return how many input tokens each request holds, and print the total."""
    tokens = [count_tokens(client, request) for request in requests]
    if model == HAIKU and max(tokens, default=0) > HAIKU_PRICE_LIMIT:
        raise ValueError("A request is too long for the Haiku price used here.")

    print(f"{len(requests)} requests, {sum(tokens):,} input tokens.", flush=True)
    return tokens


def check_budget(tokens: list[int], model: str, batch: bool, budget: float) -> None:
    """Print the most the requests could cost, and raise OverBudget if that is over budget."""
    most = worst_case_cost(tokens, model, batch)
    way = "through the Batch API" if batch else "one at a time"
    print(f"The most they could cost {way}: ${most:.2f} (budget ${budget:.2f}).")
    if most > budget:
        raise OverBudget(
            f"${most:.2f} is over the budget of ${budget:.2f}. Nothing was sent."
        )


def estimate(client, bills: list[dict], model: str) -> None:
    """Print the most the bills could cost each way. Counting is free; nothing is sent."""
    _, tokens = build_requests(client, bills, model)
    print(f"One at a time: at most ${worst_case_cost(tokens, model, batch=False):.2f}")
    print(f"Batch API:     at most ${worst_case_cost(tokens, model, batch=True):.2f}")


def actual_cost(usage, model: str, batch: bool) -> Decimal:
    """Return what one answer cost, in dollars, from the tokens it reports."""
    input_price, output_price = PRICES[model]
    # No request here asks for caching, so the two cache counts are expected
    # to be zero. They are added in so that no input token goes uncounted.
    input_tokens = (
        usage.input_tokens
        + (usage.cache_creation_input_tokens or 0)
        + (usage.cache_read_input_tokens or 0)
    )
    dollars = (
        Decimal(input_tokens) * Decimal(str(input_price))
        + Decimal(usage.output_tokens) * Decimal(str(output_price))
    ) / 1_000_000
    return dollars * Decimal(str(BATCH_DISCOUNT)) if batch else dollars


def read_answer(message) -> dict | None:
    """Return the two texts from an answer, or None if it stopped short or was declined."""
    if message.stop_reason != "end_turn":
        return None

    text = next((block.text for block in message.content if block.type == "text"), "")
    answer = json.loads(text)
    return {
        "plain_summary": answer["plain_summary"].strip(),
        "impact": answer["impact"].strip(),
    }


def store(
    conn,
    bill_id: int,
    version_code: str,
    prompt_version: str,
    message,
    cost: Decimal,
    revised_from: int | None = None,
) -> bool:
    """Store one answer as a draft, and return whether a new row was written.

    revised_from is the approved row a revision rewrites. Nothing is stored
    for an answer that was cut short or declined, or one already stored.
    """
    answer = read_answer(message)
    if answer is None:
        print(f"  bill {bill_id}: not stored, it ended with {message.stop_reason}")
        return False

    inserted = conn.execute(
        INSERT_SQL,
        answer
        | {
            "bill_id": bill_id,
            "source_version_code": version_code,
            "model": message.model,
            "prompt_version": prompt_version,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "cost_usd": cost,
            "revised_from": revised_from,
        },
    )
    return inserted.rowcount == 1


def sample(conn, client, bills: list[dict], model: str, budget: float) -> Decimal:
    """Write up the bills one request at a time, storing each answer as it arrives.

    Each answer is committed on its own, so one already paid for is kept even
    if a later request fails. Returns what the requests cost.
    """
    requests, tokens = build_requests(client, bills, model)
    check_budget(tokens, model, batch=False, budget=budget)
    spent = Decimal(0)
    with record_run(conn, JOB, f"{SOURCE} {model}") as run:
        for bill, request in zip(bills, requests, strict=True):
            message = client.messages.create(**request)
            cost = actual_cost(message.usage, model, batch=False)
            spent += cost
            bill_id, version_code = bill["bill_id"], bill["version_code"]
            stored = store(conn, bill_id, version_code, PROMPT_VERSION, message, cost)
            conn.commit()
            run.rows_loaded += stored
            label = bill_label(bill["bill_type"], bill["bill_number"])
            print(f"  {label}: ${cost:.4f}", flush=True)
            answer = read_answer(message)
            if answer is not None:
                print(f"\n{answer['plain_summary']}\n")
                print(f"How this does you:\n{answer['impact']}\n", flush=True)
    print(f"Spent ${spent:.4f}.")
    return spent


def custom_id(bill: dict) -> str:
    """Return the ID a batch request is sent with, which comes back with its answer."""
    return f"b{bill['bill_id']}-v{bill['version_code']}-p{PROMPT_VERSION}"


def submit(client, bills: list[dict], model: str, budget: float) -> str:
    """Send the bills as one batch at half price, and return the batch's ID."""
    requests, tokens = build_requests(client, bills, model)
    check_budget(tokens, model, batch=True, budget=budget)
    batch = client.messages.batches.create(
        requests=[
            {"custom_id": custom_id(bill), "params": request}
            for bill, request in zip(bills, requests, strict=True)
        ]
    )
    return batch.id


def collect(conn, client, batch_id: str) -> Decimal | None:
    """Store a finished batch's answers as drafts and return what the batch cost.

    Returns None, storing nothing, while the batch is still running. Running
    it again for the same batch stores nothing twice and costs nothing: the
    answers were paid for when the batch ran.
    """
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"Batch {batch_id} is {batch.processing_status}. Try again later.")
        return None

    spent = Decimal(0)
    with record_run(conn, JOB, f"{SOURCE} batch {batch_id}") as run:
        for result in client.messages.batches.results(batch_id):
            match = CUSTOM_ID.fullmatch(result.custom_id)
            if match is None:
                print(f"  {result.custom_id}: not an ID this command sends, skipped")
                continue
            if result.result.type != "succeeded":
                print(f"  {result.custom_id}: {result.result.type}, nothing stored")
                continue

            bill_id, version_code, prompt_version = match.groups()
            message = result.result.message
            cost = actual_cost(message.usage, message.model, batch=True)
            spent += cost
            run.rows_loaded += store(
                conn, int(bill_id), version_code, prompt_version, message, cost
            )
    print(f"The batch cost ${spent:.4f}. {run.rows_loaded} new drafts stored.")
    return spent


def revisions_due(conn, limit: int | None = None) -> list[dict]:
    """Return the approved rows with a note and no revision yet, oldest decision first."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(REVISIONS_DUE_SQL, {"limit": limit}).fetchall()


def revision_params(row: dict, model: str) -> dict:
    """Return the request to revise one approved row: the bill, its text and the note."""
    approved = json.dumps(
        {"plain_summary": row["plain_summary"], "impact": row["impact"]}, indent=2
    )
    content = (
        f"{user_message(row)}\n"
        f"\n<approved_text>\n{approved}\n</approved_text>\n"
        f"\n<editor_note>\n{row['review_note']}\n</editor_note>"
    )
    return request_params(row, model) | {
        "system": REVISION_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
    }


def revise(conn, client, rows: list[dict], model: str, budget: float) -> Decimal:
    """Rewrite each approved row to address its note, storing each revision as a draft.

    Sent one at a time, like sample, so a run started overnight is done in
    minutes. Returns what the requests cost.
    """
    if model not in PRICES:
        raise ValueError(f"No price is known for {model}.")

    requests = [revision_params(row, model) for row in rows]
    tokens = measure(client, requests, model)
    check_budget(tokens, model, batch=False, budget=budget)
    spent = Decimal(0)
    with record_run(conn, JOB, f"{SOURCE} {model} revisions") as run:
        for row, request in zip(rows, requests, strict=True):
            message = client.messages.create(**request)
            cost = actual_cost(message.usage, model, batch=False)
            spent += cost
            run.rows_loaded += store(
                conn,
                row["bill_id"],
                row["version_code"],
                REVISION_PROMPT_VERSION,
                message,
                cost,
                revised_from=row["revised_from"],
            )
            conn.commit()
            label = bill_label(row["bill_type"], row["bill_number"])
            print(f"  {label}: ${cost:.4f}", flush=True)
            answer = read_answer(message)
            if answer is not None:
                print(
                    f"\n{marked_changes(row['plain_summary'], answer['plain_summary'])}"
                )
                print(
                    f"\nHow this does you:\n{marked_changes(row['impact'], answer['impact'])}\n"
                )
    print(f"Spent ${spent:.4f}.")
    return spent


def marked_changes(old: str, new: str) -> str:
    """Return new with the words that changed marked: [-removed-] {+added+}."""
    old_words, new_words = old.split(), new.split()
    marked = []
    matcher = difflib.SequenceMatcher(a=old_words, b=new_words, autojunk=False)
    for op, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if op == "equal":
            marked.extend(new_words[new_start:new_end])
            continue
        if old_end > old_start:
            marked.append(f"[-{' '.join(old_words[old_start:old_end])}-]")
        if new_end > new_start:
            marked.append(f"{{+{' '.join(new_words[new_start:new_end])}+}}")
    return " ".join(marked)


def local_time(moment) -> str:
    """Return a stored time in this computer's time zone, such as 2026-10-18 21:07."""
    return f"{moment.astimezone():%Y-%m-%d %H:%M}"


def show_revision(draft: dict) -> None:
    """Print a revision under the note it answers, with what changed marked."""
    print(
        f"\nRevision. Approved {local_time(draft['approved_at'])}, "
        f'with note: "{draft["approved_note"]}"'
    )
    print(
        f"\nRegenerated after note ({draft['model']}, ${draft['cost_usd']:.4f}), "
        "changes marked [-removed-] {+added+}:"
    )
    print(marked_changes(draft["approved_summary"], draft["plain_summary"]))
    print("\nHow this does you:")
    print(marked_changes(draft["approved_impact"], draft["impact"]))


def review(conn, ask=input) -> int:
    """Show each draft beside its CRS summary and record the decision. Return how many were decided.

    Revisions come first. a approves, r rejects, s skips to the next, q stops.
    Approving a revision puts it on the site in place of the text it revises;
    rejecting one leaves that text as it is. Each decision is committed as it
    is made, so stopping partway loses nothing.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        drafts = cur.execute(DRAFTS_SQL).fetchall()

    decided = 0
    for number, draft in enumerate(drafts, start=1):
        label = bill_label(draft["bill_type"], draft["bill_number"])
        print(f"\n[{number} of {len(drafts)}] {label}: {draft['title']}")
        print(f"{draft['source_url']}")
        print(f"\nCRS summary ({draft['action_desc']}, {draft['action_on']}):")
        print(draft["summary_text"])
        if draft["revised_from"]:
            show_revision(draft)
            choices = "[a]pprove revision, [r]eject (keep the original), [s]kip, [q]uit"
        else:
            print(
                f"\nPlain-language summary ({draft['model']}, "
                f"${draft['cost_usd']:.4f}):"
            )
            print(draft["plain_summary"])
            print("\nHow this does you:")
            print(draft["impact"])
            choices = "[a]pprove, [r]eject, [s]kip, [q]uit"
        if draft["review_note"]:
            print(f"\nEarlier note: {draft['review_note']}")

        choice = ask(f"\n{choices}: ").strip().lower()
        if choice == "q":
            break
        if choice not in ("a", "r"):
            continue

        if draft["review_note"]:
            note = ask("Note (Enter keeps the earlier one): ").strip() or None
        else:
            note = ask("Note (Enter for none): ").strip() or None
        status = "approved" if choice == "a" else "rejected"
        conn.execute(REVIEW_SQL, {"id": draft["id"], "status": status, "note": note})
        conn.commit()
        decided += 1

    print(f"\n{decided} decided, {len(drafts) - decided} still drafts.")
    return decided


def revisit(conn, ask=input) -> int:
    """Walk the last UNDO_DAYS of decisions, newest first, and reopen any that were mistakes.

    u sends one back to the drafts, keeping its note, s skips it, q stops.
    Return how many were reopened.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        decisions = cur.execute(RECENT_DECISIONS_SQL, {"days": UNDO_DAYS}).fetchall()

    if not decisions:
        print(f"No decisions in the last {UNDO_DAYS} days.")
        return 0

    reopened = 0
    for number, decision in enumerate(decisions, start=1):
        label = bill_label(decision["bill_type"], decision["bill_number"])
        print(f"\n[{number} of {len(decisions)}] {label}: {decision['title']}")
        print(
            f"{decision['status'].capitalize()} {local_time(decision['reviewed_at'])}"
        )
        if decision["review_note"]:
            print(f"Note: {decision['review_note']}")

        choice = ask("\n[u]ndo, [s]kip, [q]uit: ").strip().lower()
        if choice == "q":
            break
        if choice != "u":
            continue

        conn.execute(REOPEN_SQL, {"id": decision["id"], "days": UNDO_DAYS})
        conn.commit()
        reopened += 1

    print(f"\n{reopened} sent back to the drafts.")
    return reopened


def bill_argument(value: str) -> str:
    """Accept a bill named as Congress/type/number, such as 119/hr/1."""
    if not BILL_ARGUMENT.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not like 119/hr/1")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("estimate", "sample", "submit"):
        command = commands.add_parser(name)
        command.add_argument("--model", choices=sorted(PRICES), default=DEFAULT_MODEL)
        command.add_argument("--limit", type=int, help="stop after this many bills")
        command.add_argument(
            "--bill",
            action="append",
            type=bill_argument,
            help="only this bill, such as 119/hr/1; give it more than once for more",
        )
        command.add_argument(
            "--budget",
            type=float,
            default=DEFAULT_BUDGET,
            help=f"refuse if the most it could cost is over this, in dollars "
            f"(default {DEFAULT_BUDGET})",
        )

    collect_command = commands.add_parser("collect")
    collect_command.add_argument("batch_id")
    revise_command = commands.add_parser("revise")
    revise_command.add_argument(
        "--model", choices=sorted(PRICES), default=DEFAULT_MODEL
    )
    revise_command.add_argument("--limit", type=int, help="stop after this many")
    revise_command.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET,
        help=f"refuse if the most it could cost is over this, in dollars "
        f"(default {DEFAULT_BUDGET})",
    )
    review_command = commands.add_parser("review")
    review_command.add_argument(
        "--previous",
        action="store_true",
        help=f"first walk the last {UNDO_DAYS} days of decisions to undo any",
    )
    args = parser.parse_args()

    with connect() as conn:
        if args.command == "review":
            if args.previous:
                revisit(conn)
            review(conn)
            return

        client = anthropic.Anthropic()
        if args.command == "collect":
            collect(conn, client, args.batch_id)
            return
        if args.command == "revise":
            rows = revisions_due(conn, args.limit)
            if not rows:
                print("No approved summaries with a note are waiting for a revision.")
                return
            print(f"Prompt version {REVISION_PROMPT_VERSION}, model {args.model}.")
            try:
                revise(conn, client, rows, args.model, args.budget)
            except OverBudget as exc:
                raise SystemExit(str(exc)) from None
            return

        bills = bills_to_write(conn, args.model, args.limit, args.bill)
        print(f"Prompt version {PROMPT_VERSION}, model {args.model}.")
        try:
            if args.command == "estimate":
                estimate(client, bills, args.model)
            elif args.command == "sample":
                sample(conn, client, bills, args.model, args.budget)
            else:
                batch_id = submit(client, bills, args.model, args.budget)
                print(f"Submitted batch {batch_id}. Collect it with:")
                print(f"  python -m ingest.summaries collect {batch_id}")
        except OverBudget as exc:
            raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
