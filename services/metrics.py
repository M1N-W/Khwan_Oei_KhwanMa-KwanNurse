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
import math
from collections import Counter
from collections import defaultdict, deque
from typing import Dict

from config import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_counters: Counter = Counter()
_latency_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))


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
        _latency_samples.clear()


def observe_latency(name: str, seconds: float) -> None:
    """Record a bounded latency sample without request payload or patient data."""
    if not name or seconds < 0:
        return
    try:
        with _lock:
            _latency_samples[name].append(round(float(seconds) * 1000, 3))
    except Exception:  # pragma: no cover - defensive only
        logger.debug("metrics.observe_latency failed for %s", name, exc_info=True)


def latency_snapshot() -> Dict[str, Dict[str, float | int]]:
    """Return in-process p50/p95 timing aggregates in milliseconds."""
    with _lock:
        samples = {name: list(values) for name, values in _latency_samples.items()}

    result: Dict[str, Dict[str, float | int]] = {}
    for name, values in samples.items():
        if not values:
            continue
        values.sort()
        def percentile(percent: float) -> float:
            index = max(0, math.ceil(len(values) * percent) - 1)
            return values[index]
        result[name] = {
            "count": len(values),
            "p50_ms": percentile(0.50),
            "p95_ms": percentile(0.95),
            "max_ms": values[-1],
        }
    return result


def log_summary() -> None:
    """Emit a single-line summary of all counters to the logger."""
    snap = snapshot()
    if not snap:
        logger.info("metrics: (empty)")
        return
    parts = [f"{k}={v}" for k, v in sorted(snap.items())]
    logger.info("metrics: %s", " ".join(parts))
    latency = latency_snapshot()
    if latency:
        logger.info("latency_metrics: %s", latency)
