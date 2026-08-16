"""Low-overhead latency instrumentation for webhook critical paths."""
from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from services.metrics import observe_latency


def latency_name(operation: str, intent: str | None = None) -> str:
    """Build a metric key from allowlisted control metadata only."""
    safe_operation = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in operation)[:80]
    if not intent:
        return safe_operation
    safe_intent = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in intent)[:64]
    return f"{safe_operation}.{safe_intent or 'unknown'}"


@contextmanager
def measure(operation: str, *, intent: str | None = None) -> Iterator[None]:
    """Record elapsed time while preserving the wrapped operation's behavior."""
    started = perf_counter()
    try:
        yield
    finally:
        observe_latency(latency_name(operation, intent), perf_counter() - started)
