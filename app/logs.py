"""The web app's logs: one line per request, every line tagged with that request's ID.

Render gives each incoming request an ID and passes it to the app in the
Rndr-Id header; its own request logs show the same ID. Tagging every line
the app writes with it means all the lines from one visit can be found
together, in the app's logs and Render's. Without Render (on a laptop, in
the tests), the app makes up an ID.

A request's line holds its method, path, status and time taken. It never
holds the query string or the body, so search words and addresses stay out.
"""

import contextvars
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

# The ID of the request being handled. A context variable keeps one value per
# request, even with many requests running at once on different threads.
request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

# An ID from a header is used only if it looks like one, so nobody can write
# a fake log line by sending an ID with a line break in it.
VALID_ID = re.compile(r"[A-Za-z0-9-]{1,64}")

LOG_FORMAT = "%(levelname)s request=%(request_id)s %(name)s: %(message)s"

logger = logging.getLogger("app.requests")


# Whether log records already get the request's ID. The app sets this up
# once, even if the module that does it is imported more than once.
records_tagged = False


def tag_records_with_request_id() -> None:
    """Give every log record, from any logger, the current request's ID."""
    global records_tagged
    if records_tagged:
        return
    make_record = logging.getLogRecordFactory()

    def make_tagged_record(*args, **kwargs) -> logging.LogRecord:
        record = make_record(*args, **kwargs)
        record.request_id = request_id.get()
        return record

    logging.setLogRecordFactory(make_tagged_record)
    records_tagged = True


class AppLogHandler(logging.StreamHandler):
    """Writes the app's log lines to standard output, where Render collects them."""


def configure_logging() -> None:
    """Send log lines at INFO and above to standard output, once."""
    root = logging.getLogger()
    if any(isinstance(handler, AppLogHandler) for handler in root.handlers):
        return
    handler = AppLogHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def id_for(request: Request) -> str:
    """Return Render's ID for the request if it sent a valid one, otherwise a new one."""
    sent = request.headers.get("rndr-id", "")
    return sent if VALID_ID.fullmatch(sent) else uuid.uuid4().hex[:16]


def add_request_ids(app: FastAPI) -> None:
    """Tag each request with an ID, log one line for it, and send the ID back as X-Request-ID."""
    tag_records_with_request_id()

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = request_id.set(id_for(request))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("%s %s failed", request.method, request.url.path)
            raise
        else:
            milliseconds = round((time.perf_counter() - started) * 1000)
            logger.info(
                "%s %s %d in %d ms",
                request.method,
                request.url.path,
                response.status_code,
                milliseconds,
            )
            response.headers["X-Request-ID"] = request_id.get()
            return response
        finally:
            request_id.reset(token)
