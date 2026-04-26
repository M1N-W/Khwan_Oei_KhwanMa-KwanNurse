# -*- coding: utf-8 -*-
"""
Sheet operation retry helpers (Phase 4 P4-3).

Google Sheets API has known transient failure modes:
- 429 RESOURCE_EXHAUSTED (per-minute quota)
- 500/503 internal errors
- Network timeouts during gspread → googleapis hop

These almost always succeed on retry within 1-2 seconds. Without retries
we silently lose audit rows: ``save_education_view`` /
``save_wound_analysis`` / ``save_symptom_data`` all swallow exceptions
and return ``False`` — meaning a transient blip costs us a permanent
data gap.

This module provides a small ``retry_sheet_op`` helper that:

- Retries up to ``max_attempts`` times (default 3)
- Uses exponential backoff with full jitter (0.5s, 1s, 2s + jitter)
- Only retries on the exception classes typically associated with
  transient gspread/googleapis failures; logic errors (TypeError,
  KeyError) propagate immediately so bugs are visible
- Records every retry + final outcome via metrics counters
  (``sheets.retry.<reason>``) so the dashboard ``/metrics`` shows
  flakiness rates

Usage::

    from database.retry import retry_sheet_op

    def _do():
        return sheet.append_row(row, value_input_option="USER_ENTERED")
    retry_sheet_op(_do, op_name="education_logs.append")

The helper returns the wrapped function's return value on success, or
re-raises the last exception after all attempts fail. Callers that
already wrap their writes in try/except (most do) get free resilience
without changing their error-handling logic.
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Tuple

from config import get_logger
from services.metrics import incr

logger = get_logger(__name__)


# Exception class names we treat as transient. We compare by string name
# so we don't have to import gspread/googleapis classes (which would make
# this module hard to test in isolation).
_TRANSIENT_NAMES = frozenset({
    "APIError",                # gspread.exceptions.APIError
    "ServerError",             # gspread legacy
    "GSpreadException",        # gspread base — we retry to be safe
    "ConnectionError",         # urllib3 / requests
    "ConnectTimeout",
    "ReadTimeout",
    "Timeout",
    "ChunkedEncodingError",
    "RemoteDisconnected",
    "ProtocolError",
    "TransportError",          # google.auth.exceptions
    "ServiceUnavailable",      # googleapiclient.errors.HttpError 503
    "TooManyRequests",         # 429
})


def _is_transient(exc: BaseException) -> bool:
    """Return True if ``exc`` looks like a transient sheet failure."""
    name = type(exc).__name__
    if name in _TRANSIENT_NAMES:
        return True
    # Some gspread errors carry HTTP status in their args/message
    msg = str(exc).lower()
    if any(token in msg for token in ("503", "502", "429", "timeout", "temporarily")):
        return True
    return False


def retry_sheet_op(
    fn: Callable[[], Any],
    *,
    op_name: str = "sheet.op",
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
) -> Any:
    """
    Run ``fn()`` with retries on transient errors. ``fn`` must take no
    arguments — wrap with ``lambda`` or ``functools.partial`` if needed.

    Args:
        fn: zero-arg callable doing the actual sheet write/read.
        op_name: short label used in metric counter names + log messages.
        max_attempts: total attempts including the first try (>=1).
        base_delay: seconds for the first backoff. Doubles each attempt.
        max_delay: cap for the (pre-jitter) backoff window.

    Returns:
        Whatever ``fn()`` returned on success.

    Raises:
        Re-raises the last exception if all attempts fail, or any
        non-transient exception immediately.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
            if attempt > 1:
                incr(f"sheets.retry.{op_name}.recovered")
                logger.info(
                    "sheets retry succeeded op=%s attempt=%d/%d",
                    op_name, attempt, max_attempts,
                )
            return result
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc):
                # Non-transient: bubble up immediately so bugs surface.
                incr(f"sheets.retry.{op_name}.non_transient")
                raise

            incr(f"sheets.retry.{op_name}.attempt")
            if attempt >= max_attempts:
                incr(f"sheets.retry.{op_name}.exhausted")
                logger.warning(
                    "sheets retry exhausted op=%s attempts=%d last_error=%s: %s",
                    op_name, max_attempts, type(exc).__name__, exc,
                )
                raise

            # Exponential backoff with full jitter.
            window = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay = random.uniform(0.0, window)
            logger.info(
                "sheets retry op=%s attempt=%d/%d sleeping=%.2fs error=%s",
                op_name, attempt, max_attempts, delay, type(exc).__name__,
            )
            time.sleep(delay)

    # Defensive: should be unreachable because the loop either returns
    # or raises, but keeps type-checkers happy.
    assert last_exc is not None
    raise last_exc


def is_transient_error(exc: BaseException) -> bool:
    """Public alias for ``_is_transient`` — useful for callers that
    want to decide their own retry policy."""
    return _is_transient(exc)
