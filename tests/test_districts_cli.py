"""Tests for the district loader's command line: which files it loads.

No test here touches the database or the network. The functions main()
would call are replaced with stand-ins that write down how they were called.
"""

from contextlib import nullcontext

import pytest

from ingest.congressional_districts import ALL_STATES, main

NEXT_CONGRESS = (
    "--vintage",
    "2026",
    "--congress",
    "120",
    "--effective-from",
    "2027-01-03",
)


@pytest.fixture
def loaded(monkeypatch):
    """Replace what main() would run, and return the files it loaded."""
    names = []

    def fake_load_state(conn, name, vintage, congress, effective_from):
        names.append(name)
        return 1

    monkeypatch.setattr("ingest.congressional_districts.connect", nullcontext)
    monkeypatch.setattr("ingest.congressional_districts.load_state", fake_load_state)
    return names


def run(monkeypatch, *arguments: str) -> None:
    """Call main() as if these arguments had been typed after the command."""
    monkeypatch.setattr("sys.argv", ["ingest.congressional_districts", *arguments])
    main()


def test_missouris_120th_file_is_skipped_with_the_reason(loaded, monkeypatch, capsys):
    run(monkeypatch, "--state", "29", *NEXT_CONGRESS)

    printed = capsys.readouterr().out
    assert loaded == []
    assert "SKIPPED tl_2026_29_cd120" in printed
    assert "2022 map" in printed


def test_every_other_120th_file_still_loads(loaded, monkeypatch):
    run(monkeypatch, "--state", "all", *NEXT_CONGRESS)

    assert len(loaded) == len(ALL_STATES) - 1
    assert "tl_2026_29_cd120" not in loaded
    assert "tl_2026_17_cd120" in loaded


def test_missouris_119th_file_is_not_skipped(loaded, monkeypatch):
    run(monkeypatch, "--state", "29")

    assert loaded == ["tl_2025_29_cd119"]
