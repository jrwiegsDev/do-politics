"""Tests for the photos loader.

No test downloads anything: the download and the Congress.gov request are
replaced, and the photos are a few made-up bytes that start like a JPEG.
"""

import hashlib
import io
import urllib.error
from email.message import Message

import psycopg
import pytest
from helpers import JPEG, add, add_photo, last_run, official

from ingest import photos
from ingest.members import UPSERT_SQL as UPSERT_MEMBER_SQL
from ingest.photos import (
    MAX_BYTES,
    UNITEDSTATES_URL,
    check_image,
    download,
    find_photo,
    load,
    to_row,
)

SENATOR = "T000010"
REP = "T000012"
NOBODY = "T000099"
CONGRESS_GOV_URL = "https://www.congress.gov/img/member/t000012_200.jpg"
CREDIT_HTML = '<a href="https://example.test/office">Courtesy U.S. House Office</a>'


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test", code, "error", Message(), None
    )


def add_seated(conn, *bioguide_ids) -> None:
    """Seat made-up officials, each also in members, as a photo must belong to one."""
    for bioguide_id in bioguide_ids:
        member = {
            "bioguide_id": bioguide_id,
            "lis_id": None,
            "name": bioguide_id,
            "source": "legislators-current",
        }
        conn.execute(UPSERT_MEMBER_SQL, member)
        add(conn, official(bioguide_id, bioguide_id, "senate"))


def stored(conn, bioguide_id) -> dict | None:
    row = conn.execute(
        "SELECT image, sha256, source, source_url, attribution"
        " FROM member_photos WHERE bioguide_id = %s",
        [bioguide_id],
    ).fetchone()
    if row is None:
        return None
    columns = ["image", "sha256", "source", "source_url", "attribution"]
    return dict(zip(columns, row, strict=True))


class FakeResponse(io.BytesIO):
    """Stands in for what urlopen returns: a file to read, used in a with block."""


def test_a_jpeg_is_accepted():
    check_image(JPEG)


def test_something_that_is_not_a_jpeg_is_refused():
    with pytest.raises(ValueError, match="not a JPEG"):
        check_image(b"<html>Not found</html>")


def test_an_image_over_the_limit_is_refused():
    with pytest.raises(ValueError, match="over"):
        check_image(JPEG + b"x" * MAX_BYTES)


def test_download_returns_the_image(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: FakeResponse(JPEG)
    )

    assert download("https://example.test/a.jpg") == JPEG


def test_download_sends_our_user_agent(monkeypatch):
    sent = {}

    def urlopen(request, timeout):
        sent["agent"] = request.get_header("User-agent")
        return FakeResponse(JPEG)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    download("https://example.test/a.jpg")

    assert sent["agent"].startswith("DoPolitics")


def test_download_returns_none_when_there_is_no_file(monkeypatch):
    def urlopen(request, timeout):
        raise http_error(404)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert download("https://example.test/a.jpg") is None


def test_download_raises_any_other_error(monkeypatch):
    def urlopen(request, timeout):
        raise http_error(500)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    with pytest.raises(urllib.error.HTTPError):
        download("https://example.test/a.jpg")


def test_download_refuses_a_page_that_is_not_an_image(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(b"<html>Error</html>"),
    )

    with pytest.raises(ValueError):
        download("https://example.test/a.jpg")


def test_a_row_carries_the_fingerprint_of_its_image():
    row = to_row(REP, JPEG, "unitedstates", "https://example.test/a.jpg", None)

    assert row["sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert len(row["sha256"]) == 64


def test_unitedstates_images_is_asked_first(monkeypatch):
    monkeypatch.setattr(photos, "download", lambda url: JPEG)

    def fetch_json(path):
        raise AssertionError("Congress.gov should not be asked")

    monkeypatch.setattr(photos, "fetch_json", fetch_json)

    row = find_photo(REP)

    assert row is not None
    assert row["source"] == "unitedstates"
    assert row["source_url"] == UNITEDSTATES_URL.format(bioguide_id=REP)
    assert row["attribution"] is None


def test_congress_gov_fills_a_gap_and_its_credit_is_kept_as_words(monkeypatch):
    monkeypatch.setattr(
        photos, "download", lambda url: JPEG if url == CONGRESS_GOV_URL else None
    )
    monkeypatch.setattr(
        photos,
        "fetch_json",
        lambda path: {
            "member": {
                "depiction": {"imageUrl": CONGRESS_GOV_URL, "attribution": CREDIT_HTML}
            }
        },
    )

    row = find_photo(REP)

    assert row is not None
    assert row["source"] == "congress.gov"
    assert row["source_url"] == CONGRESS_GOV_URL
    assert row["attribution"] == "Courtesy U.S. House Office"


def test_a_photo_from_congress_gov_without_a_credit_has_none(monkeypatch):
    monkeypatch.setattr(
        photos, "download", lambda url: JPEG if url == CONGRESS_GOV_URL else None
    )
    monkeypatch.setattr(
        photos,
        "fetch_json",
        lambda path: {"member": {"depiction": {"imageUrl": CONGRESS_GOV_URL}}},
    )

    row = find_photo(REP)

    assert row is not None
    assert row["attribution"] is None


@pytest.mark.parametrize(
    "member",
    [
        {},
        {"depiction": None},
        {"depiction": {"attribution": CREDIT_HTML}},
    ],
)
def test_no_photo_when_congress_gov_has_none_either(monkeypatch, member):
    monkeypatch.setattr(photos, "download", lambda url: None)
    monkeypatch.setattr(photos, "fetch_json", lambda path: {"member": member})

    assert find_photo(REP) is None


def test_no_photo_when_congress_gov_does_not_know_the_member(monkeypatch):
    monkeypatch.setattr(photos, "download", lambda url: None)

    def fetch_json(path):
        raise http_error(404)

    monkeypatch.setattr(photos, "fetch_json", fetch_json)

    assert find_photo(NOBODY) is None


@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(photos, "PAUSE_SECONDS", 0)


def photos_from(monkeypatch, found: dict) -> None:
    """Make find_photo answer from a dict of bioguide ID to image."""

    def find(bioguide_id):
        image = found.get(bioguide_id)
        if image is None:
            return None
        return to_row(bioguide_id, image, "unitedstates", "https://example.test", None)

    monkeypatch.setattr(photos, "find_photo", find)


def test_load_saves_a_photo_of_each_sitting_member(
    committing_conn, monkeypatch, no_pause
):
    add_seated(committing_conn, SENATOR, REP, NOBODY)
    committing_conn.commit()
    photos_from(monkeypatch, {SENATOR: JPEG, REP: JPEG + b"2"})

    saved, unchanged, missing = load(committing_conn)

    assert (saved, unchanged, missing) == (2, 0, [NOBODY])
    rep = stored(committing_conn, REP)
    assert rep is not None
    assert rep["image"] == JPEG + b"2"
    assert last_run(committing_conn) | {"finished_at": None} == {
        "job": "photos",
        "source": "unitedstates/images, congress.gov",
        "status": "succeeded",
        "rows_loaded": 2,
        "error": None,
        "finished_at": None,
    }


def test_a_second_load_writes_only_the_photos_that_changed(
    committing_conn, monkeypatch, no_pause
):
    add_seated(committing_conn, SENATOR, REP)
    committing_conn.commit()
    photos_from(monkeypatch, {SENATOR: JPEG, REP: JPEG})
    load(committing_conn)

    photos_from(monkeypatch, {SENATOR: JPEG, REP: JPEG + b"new"})
    saved, unchanged, missing = load(committing_conn)

    assert (saved, unchanged, missing) == (1, 1, [])
    rep = stored(committing_conn, REP)
    assert rep is not None
    assert rep["sha256"] == hashlib.sha256(JPEG + b"new").hexdigest()


def test_load_stops_after_the_limit(committing_conn, monkeypatch, no_pause):
    add_seated(committing_conn, SENATOR, REP)
    committing_conn.commit()
    photos_from(monkeypatch, {SENATOR: JPEG, REP: JPEG})

    saved, _, _ = load(committing_conn, limit=1)

    assert saved == 1
    assert stored(committing_conn, REP) is None


def test_a_failed_download_fails_the_run_and_keeps_the_photos_saved_before_it(
    committing_conn, monkeypatch, no_pause
):
    add_seated(committing_conn, SENATOR, REP)
    committing_conn.commit()

    def find(bioguide_id):
        if bioguide_id == REP:
            raise http_error(500)
        return to_row(bioguide_id, JPEG, "unitedstates", "https://example.test", None)

    monkeypatch.setattr(photos, "find_photo", find)

    with pytest.raises(urllib.error.HTTPError):
        load(committing_conn)

    assert stored(committing_conn, SENATOR) is not None
    assert last_run(committing_conn)["status"] == "failed"


def test_the_table_refuses_an_empty_image(conn):
    add_seated(conn, REP)

    with pytest.raises(psycopg.errors.CheckViolation):
        add_photo(conn, REP, image=b"")


def test_the_table_refuses_a_fingerprint_that_is_not_one(conn):
    add_seated(conn, REP)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO member_photos (bioguide_id, image, sha256, source, source_url)"
            " VALUES (%s, %s, 'abc', 'unitedstates', 'https://example.test')",
            [REP, JPEG],
        )


def test_a_photo_must_belong_to_a_member(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_photo(conn, NOBODY)
