"""Tests for the votes loader's command line: which arguments go together.

No test here touches the database or the network. The functions main()
would call are replaced with stand-ins that write down how they were called.
"""

from contextlib import nullcontext

import pytest

from ingest.votes import main

UNKNOWN_VOTE = {
    "chamber": "house",
    "question": "On Something New",
    "source_url": "https://example.test/roll001.xml",
}


@pytest.fixture
def calls(monkeypatch):
    """Replace what main() would run, and return the list of calls it made."""
    made = []

    def fake_load(conn, chamber, congress, session, limit=None):
        made.append(("load", chamber, congress, session, limit))
        return 3

    def fake_load_current(conn, chamber, today, limit=None):
        made.append(("load_current", chamber, limit))
        return 5

    def fake_recategorize(conn):
        made.append(("recategorize",))
        return 2, [UNKNOWN_VOTE]

    monkeypatch.setattr("ingest.votes.connect", lambda: nullcontext())
    monkeypatch.setattr("ingest.votes.load", fake_load)
    monkeypatch.setattr("ingest.votes.load_current", fake_load_current)
    monkeypatch.setattr("ingest.votes.recategorize", fake_recategorize)
    return made


def run(monkeypatch, *arguments: str) -> None:
    """Call main() as if these arguments had been typed after the command."""
    monkeypatch.setattr("sys.argv", ["ingest.votes", *arguments])
    main()


def test_congress_and_session_together_load_that_session(calls, monkeypatch, capsys):
    run(monkeypatch, "--chamber", "house", "--congress", "119", "--session", "1")

    printed = capsys.readouterr().out
    assert calls == [("load", "house", 119, 1, None)]
    assert "3 house votes loaded from house-clerk" in printed
    assert "Congress 119, session 1" in printed


def test_neither_congress_nor_session_loads_the_current_sessions(
    calls, monkeypatch, capsys
):
    run(monkeypatch, "--chamber", "senate", "--limit", "2")

    printed = capsys.readouterr().out
    assert calls == [("load_current", "senate", 2)]
    assert "5 senate votes loaded from senate-lis" in printed


@pytest.mark.parametrize("arguments", [["--congress", "119"], ["--session", "1"]])
def test_congress_and_session_must_be_given_together(
    calls, monkeypatch, capsys, arguments
):
    with pytest.raises(SystemExit) as stopped:
        run(monkeypatch, "--chamber", "house", *arguments)

    assert stopped.value.code == 2
    assert "give both --congress and --session, or neither" in capsys.readouterr().err
    assert calls == []


def test_missing_chamber_is_refused(calls, monkeypatch, capsys):
    with pytest.raises(SystemExit) as stopped:
        run(monkeypatch)

    assert stopped.value.code == 2
    assert "--chamber is required" in capsys.readouterr().err
    assert calls == []


def test_session_other_than_one_or_two_is_refused(calls, monkeypatch):
    with pytest.raises(SystemExit) as stopped:
        run(monkeypatch, "--chamber", "house", "--congress", "119", "--session", "3")

    assert stopped.value.code == 2
    assert calls == []


def test_recategorize_needs_no_chamber_and_loads_nothing(calls, monkeypatch, capsys):
    run(monkeypatch, "--recategorize")

    printed = capsys.readouterr().out
    assert calls == [("recategorize",)]
    assert "2 votes changed category" in printed
    assert "no rule for house question 'On Something New'" in printed
