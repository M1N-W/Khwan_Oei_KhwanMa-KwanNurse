# -*- coding: utf-8 -*-
"""
Request correlation + structured logging (Phase 4 P4-2).

Goals:

1. **Request ID propagation** — every incoming HTTP request gets a UUID
   stored on Flask's ``g`` object. The same ID is:
   - included in every log line emitted during that request (via filter)
   - returned in the ``X-Request-ID`` response header
   - taken from an inbound ``X-Request-ID`` header if the caller already
     sent one (Render injects ``Render-Request-Id`` we also pick up)
   This lets ops trace a single message end-to-end across LINE → webhook
   → fulfillment → reply, even with multiple concurrent users.

2. **Optional JSON structured logging** — toggle via ``LOG_FORMAT=json``.
   Each line becomes ``{"ts","level","logger","msg","request_id",...}``
   so Render's log search and external aggregators (Datadog, BetterStack)
   can index by field. Plain-text remains the default to keep local dev
   ergonomic.

3. **No behavior change when not in a request context** — modules that
   import logging at startup still get a ``request_id`` of ``-`` so
   formatters never crash.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from flask import Flask, g, request


# Header names we accept for inbound correlation IDs (case-insensitive
# in Flask request.headers anyway). Render adds Render-Request-Id and
# many proxies use X-Request-ID — we honor both, X-Request-ID wins.
_INBOUND_HEADERS = ("X-Request-ID", "X-Request-Id", "Render-Request-Id")
_OUTBOUND_HEADER = "X-Request-ID"


# -----------------------------------------------------------------------------
# Logging filter — injects request_id into every LogRecord
# -----------------------------------------------------------------------------
class RequestIdFilter(logging.Filter):
    """
    Adds ``record.request_id`` to every LogRecord. Falls back to ``-``
    when there is no Flask request context (background scheduler, CLI).
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # ``g`` is a LocalProxy — accessing outside a request raises.
            record.request_id = getattr(g, "request_id", "-") or "-"
        except RuntimeError:
            record.request_id = "-"
        return True


# -----------------------------------------------------------------------------
# JSON formatter
# -----------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    """
    Minimal JSON line formatter. One log record = one JSON object on
    a single line, suitable for Render log ingestion.

    Fields:
    - ``ts``: ISO-8601 timestamp
    - ``level``: log level name
    - ``logger``: ``record.name``
    - ``msg``: rendered message (after ``%`` interpolation)
    - ``request_id``: from ``RequestIdFilter`` (always present)
    - ``exc``: traceback string when ``exc_info`` is set
    - any extra fields passed via ``logger.X(..., extra={...})``
    """
    # Built-in LogRecord attributes we should NOT serialize as "extras"
    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "request_id", "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Pass through any custom ``extra=`` fields the caller attached
        for key, value in record.__dict__.items():
            if key in self._RESERVED:
                continue
            try:
                json.dumps(value)  # ensure JSON-serializable
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        return json.dumps(payload, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Bootstrap helpers
# -----------------------------------------------------------------------------
def configure_logging() -> None:
    """
    Install ``RequestIdFilter`` on the root logger and switch to JSON
    formatter when ``LOG_FORMAT=json``.

    Idempotent — calling twice is harmless. Safe to call from
    ``create_app`` after ``logging.basicConfig`` has already run in
    ``config.py``.
    """
    root = logging.getLogger()

    # Install request_id filter on every existing handler if missing
    has_filter = any(isinstance(f, RequestIdFilter) for h in root.handlers for f in h.filters)
    if not has_filter:
        rid_filter = RequestIdFilter()
        for handler in root.handlers:
            handler.addFilter(rid_filter)

    log_format = os.environ.get("LOG_FORMAT", "text").lower()
    if log_format == "json":
        json_fmt = JsonFormatter()
        for handler in root.handlers:
            handler.setFormatter(json_fmt)
    else:
        # Text format with request_id prefix when available
        text_fmt = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] [rid=%(request_id)s] %(message)s"
        )
        for handler in root.handlers:
            handler.setFormatter(text_fmt)


def register_request_id_middleware(app: Flask) -> None:
    """
    Hook ``before_request`` + ``after_request`` so every request:
    - has ``g.request_id`` set (from inbound header or fresh UUID4)
    - returns the same ID in the response ``X-Request-ID`` header
    """
    @app.before_request
    def _assign_request_id() -> None:
        rid = ""
        for header in _INBOUND_HEADERS:
            value = request.headers.get(header)
            if value:
                rid = value.strip()[:64]  # cap length
                break
        if not rid:
            rid = uuid.uuid4().hex
        g.request_id = rid

    @app.after_request
    def _propagate_request_id(response):
        try:
            rid = getattr(g, "request_id", None)
        except RuntimeError:
            rid = None
        if rid:
            response.headers[_OUTBOUND_HEADER] = rid
        return response
