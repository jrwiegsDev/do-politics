"""Tests for turning the HTML of an official summary into plain text."""

from ingest.plain_text import html_to_text


def test_paragraphs_are_set_apart_by_a_blank_line():
    assert html_to_text("<p>One.</p><p>Two.</p>") == "One.\n\nTwo."


def test_each_list_item_gets_its_own_line_and_a_dash():
    html = "<p>The House</p><ul><li>condemns it;</li><li>reaffirms it.</li></ul>"

    assert html_to_text(html) == "The House\n\n- condemns it;\n- reaffirms it."


def test_a_paragraph_after_a_list_is_set_apart_from_it():
    html = "<ul><li>one</li><li>two</li></ul><p>After the list.</p>"

    assert html_to_text(html) == "- one\n- two\n\nAfter the list."


def test_a_line_break_starts_a_new_line():
    assert html_to_text("<p>One<br/>Two</p>") == "One\nTwo"


def test_a_line_break_at_the_end_leaves_no_blank_line():
    assert html_to_text("<p>The end. <br/> </p>") == "The end."


def test_bold_and_italics_keep_their_words_and_lose_their_tags():
    html = "<p><strong>Some Act</strong></p><p>A <em>bill</em> for <b>x</b> and <i>y</i>.</p>"

    assert html_to_text(html) == "Some Act\n\nA bill for x and y."


def test_a_link_keeps_its_words_and_loses_its_address():
    html = '<p>See <a href="https://example.gov/order">Executive Order 1</a>, as amended.</p>'

    text = html_to_text(html)

    assert text == "See Executive Order 1, as amended."
    assert "example.gov" not in text


def test_codes_become_the_characters_they_stand_for():
    html = "<p>Fish &amp; Wildlife renamed&nbsp;the refuge.</p>"

    assert html_to_text(html) == "Fish & Wildlife renamed the refuge."


def test_line_breaks_in_the_source_count_as_spaces():
    html = "<p>One\n   two</p>\n<p>Three</p>"

    assert html_to_text(html) == "One two\n\nThree"


def test_text_with_no_tags_comes_back_unchanged():
    assert html_to_text("Just a sentence.") == "Just a sentence."


def test_an_empty_summary_gives_empty_text():
    assert html_to_text("") == ""


def test_the_result_is_text_and_not_safe_html():
    # A coded '<' becomes a real one, so whatever displays this text must
    # escape it. The conversion makes plain text; it does not make safe HTML.
    assert html_to_text("<p>Fewer than &lt;b&gt; five</p>") == "Fewer than <b> five"
