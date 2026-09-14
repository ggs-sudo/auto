"""Debug logging for the harness.

Off by default and entirely inert: `auto --debug ...` or `AUTO_DEBUG=1` turns
it on. Every module logs through `logging.getLogger(__name__)` under the
`auto` namespace, so enabling the `auto` logger enables them all at once.

Lines go to stderr, never stdout: stdout carries the CLI's own output, some of
it JSON. Timestamps carry milliseconds because several sessions run at once
and interleaving is exactly what the log is for.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

DEBUG_ENV_VAR = "AUTO_DEBUG"

_FORMAT = "%(asctime)s.%(msecs)03d [debug] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_HANDLER_MARKER = "auto_debug_handler"
"""Set on the handler this module installs, so enabling twice — the flag and
the env var together, or a test after the CLI — never doubles the lines."""


def enable_debug_logging(stream: TextIO | None = None) -> None:
    """Turn on debug lines for everything under the `auto` logger. Idempotent."""
    logger = logging.getLogger("auto")
    if any(
        getattr(handler, _HANDLER_MARKER, False) for handler in logger.handlers
    ):
        return
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
