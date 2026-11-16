"""Where the loaders' log lines go: standard output, where Render collects them.

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
