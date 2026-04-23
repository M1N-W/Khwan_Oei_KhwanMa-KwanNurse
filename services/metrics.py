# -*- coding: utf-8 -*-
"""
Lightweight in-process metrics counters.

Intentionally tiny — no external dependency, no network, no threads. Values
live in a module-level ``Counter`` so they reset at each process restart
(fine for our single-node Render deploy).

Design goals:
- Zero import-time side effects (safe to import from any module).
- Thread-safe increments (Flask + APScheduler can both call these).
- Easy to inspect: ``GET /metrics`` route returns the snapshot as JSON.
- Cheap periodic log summary via ``log_summary()`` — scheduler can call it
  hourly so Render log search works as a poor-man's dashboard.

Example::

    from services.metrics import incr
    incr("llm.call_success")
    incr("early_warning.alert_sent", by=1)
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import Dict

from config import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_counters: Counter = Counter()


def incr(name: str, by: int = 1) -> None:
    """Increment ``name`` by ``by`` (default 1). Never raises."""
    if not name:
        return
    try:
        with _lock:
            _counters[name] += int(by)
    except Exception:  # pragma: no cover — defensive only
        logger.debug("metrics.incr failed for %s", name, exc_info=True)


def snapshot() -> Dict[str, int]:
    """Return a copy of the current counter state."""
    with _lock:
        return dict(_counters)


def reset() -> None:
    """Reset all counters. Intended for tests only."""
    with _lock:
        _counters.clear()


def log_summary() -> None:
    """Emit a single-line summary of all counters to the logger."""
    snap = snapshot()
    if not snap:
        logger.info("metrics: (empty)")
        return
    parts = [f"{k}={v}" for k, v in sorted(snap.items())]
    logger.info("metrics: %s", " ".join(parts))
