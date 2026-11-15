"""Tests for request IDs: one log line per request, every line tagged, the ID sent back."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.logs import LOG_FORMAT
from app.main import app, get_conn


class LoggingConnection:
    """Stands in for a database connection, and logs when a route uses it."""

    def execute(self, query):
        logging.getLogger("app.test").info("query ran: %s", query)


@pytest.fixture
def client():
    app.dependency_overrides[get_conn] = lambda: LoggingConnection()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def request_lines(caplog):
    return [r for r in caplog.records if r.name == "app.requests"]


def test_render_s_request_id_is_used_and_sent_back(client, caplog):
    with caplog.at_level(logging.INFO):
        response = client.get("/health", headers={"Rndr-Id": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
    [line] = request_lines(caplog)
    assert line.request_id == "abc-123"
    assert line.getMessage().startswith("GET /health 200 in ")


def test_without_render_an_id_is_made_up(client):
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]

    assert len(first) == 16
    assert first != second


@pytest.mark.parametrize(
    "sent", ["abc\nINFO request=fake app: forged line", "x" * 65, "has space", ""]
)
def test_an_id_that_does_not_look_like_one_is_replaced(client, sent):
    response = client.get("/health", headers={"Rndr-Id": sent})

    assert response.headers["X-Request-ID"] != sent
    assert "\n" not in response.headers["X-Request-ID"]


def test_a_line_written_inside_a_route_carries_the_same_id(client, caplog):
    # Routes run on a worker thread; the ID must follow the request there.
    with caplog.at_level(logging.INFO):
        client.get("/health", headers={"Rndr-Id": "abc-123"})

    [query_line] = [r for r in caplog.records if r.name == "app.test"]
    assert query_line.request_id == "abc-123"


def test_lines_outside_any_request_are_tagged_with_a_dash(caplog):
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.test").info("starting up")

    assert caplog.records[-1].request_id == "-"


def test_a_failed_request_is_logged_with_its_id_and_still_answers_500(client, caplog):
    app.dependency_overrides[get_conn] = lambda: None

    with caplog.at_level(logging.INFO):
        response = client.get("/health", headers={"Rndr-Id": "abc-123"})

    assert response.status_code == 500
    [line] = request_lines(caplog)
    assert line.levelname == "ERROR"
    assert line.request_id == "abc-123"
    assert line.getMessage() == "GET /health failed"


def test_the_line_never_holds_the_query_string(client, caplog):
    with caplog.at_level(logging.INFO):
        client.get("/health?q=veterans+benefits")

    [line] = request_lines(caplog)
    assert "veterans" not in line.getMessage()


def test_the_log_format_shows_the_id():
    record = logging.LogRecord(
        "app.requests", logging.INFO, "", 0, "GET / 200", None, None
    )
    record.request_id = "abc-123"

    line = logging.Formatter(LOG_FORMAT).format(record)

    assert line == "INFO request=abc-123 app.requests: GET / 200"
