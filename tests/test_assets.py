"""Tests for fingerprinted static file addresses and how long browsers may keep them."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import assets
from app.assets import CHECK_FIRST, KEEP_FOR_A_YEAR, fingerprint, static_url
from app.main import app, get_conn

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
STATIC_FILES = sorted(p.name for p in assets.STATIC_DIR.iterdir() if p.is_file())


@pytest.fixture
def client(conn):
    """A client whose requests use the test database (the front page reads topics)."""
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_fingerprint_is_the_start_of_the_files_sha256():
    contents = (assets.STATIC_DIR / "style.css").read_bytes()

    assert fingerprint("style.css") == hashlib.sha256(contents).hexdigest()[:12]
    assert static_url("style.css") == f"/static/style.css?v={fingerprint('style.css')}"


def test_a_changed_file_gets_a_new_fingerprint_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    sheet = tmp_path / "style.css"
    sheet.write_text("body { color: black; }")
    before = fingerprint("style.css")

    sheet.write_text("body { color: darkblue; }")

    assert fingerprint("style.css") != before


def test_the_same_file_keeps_the_same_fingerprint():
    assert fingerprint("htmx.min.js") == fingerprint("htmx.min.js")


@pytest.mark.parametrize("name", STATIC_FILES)
def test_a_file_asked_for_by_its_fingerprint_may_be_kept_a_year(client, name):
    response = client.get(static_url(name))

    assert response.status_code == 200
    assert response.headers["cache-control"] == KEEP_FOR_A_YEAR


@pytest.mark.parametrize(
    "address",
    ["/static/style.css", "/static/style.css?v=000000000000", "/static/style.css?x=1"],
    ids=["no fingerprint", "stale fingerprint", "other query"],
)
def test_any_other_address_must_be_checked_before_reuse(client, address):
    response = client.get(address)

    assert response.status_code == 200
    assert response.headers["cache-control"] == CHECK_FIRST


def test_an_unchanged_file_answers_304_and_keeps_its_cache_rule(client):
    first = client.get(static_url("style.css"))

    again = client.get(
        static_url("style.css"), headers={"If-None-Match": first.headers["etag"]}
    )

    assert again.status_code == 304
    assert again.headers["cache-control"] == KEEP_FOR_A_YEAR


@pytest.mark.parametrize("path", ["/", "/privacy", "/terms"])
def test_every_page_links_the_stylesheet_and_script_by_fingerprint(client, path):
    page = client.get(path).text

    assert f'href="{static_url("style.css")}"' in page
    assert f'src="{static_url("htmx.min.js")}"' in page


def test_no_template_links_a_static_file_without_its_fingerprint():
    # A bare /static/ link would be checked on every use instead of kept, and
    # could reach a visitor stale after a deploy that changed it.
    for template in TEMPLATES.glob("*.html"):
        assert '"/static/' not in template.read_text(), template.name
