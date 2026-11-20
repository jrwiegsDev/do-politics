"""Where the loaders' log lines go, and how they word what they print.

Log lines go to standard output, where Render collects them.

Helpers such as ingest.fetch only log; each loader's main calls
configure_logging once, so the lines come out in the same shape as the web
app's, without its request IDs. print and the log lines share sys.stdout, so
they stay in the order they were written.
"""

import logging
import sys

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """Send log lines at INFO and above to standard output.

    basicConfig does nothing once logging has a handler, so calling this
    twice, or under pytest, adds no second copy of each line.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)


def counted(count: int, singular: str, plural: str | None = None) -> str:
    """Return a count with its noun, such as '1 bill', '563 bills' or '2 summaries'.

    plural is needed only when adding 's' is wrong, as for 'summary'.
    """
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural or singular + 's'}"


def print_list(heading: str, items: list[str]) -> None:
    """Print a heading, then each item indented under it, or nothing if there are none.

    Every loader ends a run this way, so an indented line always belongs to
    the line above it, and the heading says how many there are and what
    they are: '1 bill skipped until the next run (...):'.
    """
    if not items:
        return
    print(heading)
    for item in items:
        print(f"  {item}")
