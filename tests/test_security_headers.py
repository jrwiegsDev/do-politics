"""Tests that every response carries the security headers, with the right policy per page."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import DOCS_POLICY, HEADERS, SITE_POLICY, policy_for

client = TestClient(app)


@pytest.mark.parametrize(
    "path",
    ["/", "/privacy", "/static/style.css", "/openapi.json", "/no-such-page"],
)
def test_every_response_carries_the_headers(path):
    response = client.get(path)

    assert response.headers["Content-Security-Policy"] == SITE_POLICY
    for name, value in HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_the_api_docs_get_their_own_policy(path):
    assert client.get(path).headers["Content-Security-Policy"] == DOCS_POLICY


def test_the_site_policy_allows_only_this_site():
    assert "'unsafe-inline'" not in SITE_POLICY
    assert "https://" not in SITE_POLICY
    assert "frame-ancestors 'none'" in SITE_POLICY


def test_only_the_docs_paths_get_the_looser_policy():
    assert policy_for("/docs") == DOCS_POLICY
    assert policy_for("/docs/../") == SITE_POLICY
    assert policy_for("/documents") == SITE_POLICY
