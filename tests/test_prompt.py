"""Tests for which part of the editorial policy Claude is given, and the prompt version."""

import pytest

from ingest.prompt import (
    REVISION_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    fingerprint,
    system_prompt,
    writing_rules,
)

POLICY = """\
# Editorial policy

Intro.

## Where the words come from

- Official summaries.

## How we write about a bill

- Rule one.
- Rule two.

## Corrections

Email us.
"""


def test_only_the_writing_rules_section_is_taken():
    assert (
        writing_rules(POLICY)
        == "## How we write about a bill\n\n- Rule one.\n- Rule two.\n"
    )


def test_the_section_can_be_the_last_one():
    policy = "# Policy\n\n## How we write about a bill\n\n- Only rule.\n"

    assert writing_rules(policy) == "## How we write about a bill\n\n- Only rule.\n"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("Email us.", "Write to privacy@example.test."),
        ("- Official summaries.", "- Official summaries, unchanged."),
        ("Intro.", "A new introduction."),
    ],
)
def test_editing_another_section_keeps_the_prompt_version(old, new):
    edited = POLICY.replace(old, new)

    assert fingerprint(system_prompt(edited)) == fingerprint(system_prompt(POLICY))


def test_editing_a_writing_rule_changes_the_prompt_version():
    edited = POLICY.replace("- Rule two.", "- Rule two, reworded.")

    assert fingerprint(system_prompt(edited)) != fingerprint(system_prompt(POLICY))


def test_a_policy_without_the_heading_is_refused():
    with pytest.raises(ValueError, match="How we write about a bill"):
        writing_rules(POLICY.replace("## How we write about a bill", "## Our rules"))


def test_the_real_prompt_has_the_rules_and_not_the_other_sections():
    assert "We say plainly who a bill helps" in SYSTEM_PROMPT
    assert "## Where the words come from" not in SYSTEM_PROMPT
    assert "## Corrections" not in SYSTEM_PROMPT


@pytest.mark.parametrize("prompt", [SYSTEM_PROMPT, REVISION_SYSTEM_PROMPT])
def test_the_reader_is_put_in_a_bill_for_students_whatever_their_age(prompt):
    # Students, trainees and new graduates can be adults going back to
    # school, so the example puts the reader in the scene, not only a child.
    assert '"Imagine you or your child..."' in prompt
    assert '"Imagine your child..."' not in prompt
    assert (
        "put a child in the scene alone only when the bill reaches only children"
        in prompt
    )
    assert "keep to that through the rest of impact" in prompt
