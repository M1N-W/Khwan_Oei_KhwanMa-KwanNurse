# -*- coding: utf-8 -*-
"""
Phase 4 P4-2: request correlation + structured logging + error metrics.

Coverage:
1. RequestIdFilter injects request_id (or '-' outside request)
2. JsonFormatter emits valid JSON with required fields
3. /webhook generates X-Request-ID header
4. /webhook honors inbound X-Request-ID
5. /webhook honors Render-Request-Id fallback
6. /webhook caps inbound request_id length
7. Intent dispatch increments webhook.intent.<name>
8. Failing handler increments webhook.error.<name>
9. Successful dispatch does NOT increment error counter
10. JsonFormatter passes through extra fields
"""
from __future__ import annotations

import os
import sys
import json
import logging
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# 1-2. Pure logging components
# -----------------------------------------------------------------------------
class LoggingComponentsTests(unittest.TestCase):

    def test_request_id_filter_outside_context_returns_dash(self):
        from services.observability import RequestIdFilter
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        RequestIdFilter().filter(rec)
        self.assertEqual(rec.request_id, "-")

    def test_request_id_filter_inside_context_uses_g(self):
        from flask import Flask, g
        from services.observability import RequestIdFilter

        app = Flask(__name__)
        with app.test_request_context("/"):
            g.request_id = "abc-123"
            rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
            RequestIdFilter().filter(rec)
            self.assertEqual(rec.request_id, "abc-123")

    def test_json_formatter_emits_valid_json(self):
        from services.observability import JsonFormatter
        rec = logging.LogRecord("svc", logging.INFO, __file__, 42, "hello %s", ("world",), None)
        rec.request_id = "rid-1"
        line = JsonFormatter().format(rec)
        payload = json.loads(line)
        self.assertEqual(payload["msg"], "hello world")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "svc")
        self.assertEqual(payload["request_id"], "rid-1")
        self.assertIn("ts", payload)

    def test_json_formatter_passes_extra_fields(self):
        from services.observability import JsonFormatter
        rec = logging.LogRecord("svc", logging.INFO, __file__, 1, "x", None, None)
        rec.request_id = "-"
        rec.user_id_short = "U-ab***cd"
        rec.intent = "ReportSymptoms"
        line = JsonFormatter().format(rec)
        payload = json.loads(line)
        self.assertEqual(payload["user_id_short"], "U-ab***cd")
        self.assertEqual(payload["intent"], "ReportSymptoms")

    def test_json_formatter_handles_unserializable_extra(self):
        from services.observability import JsonFormatter
        rec = logging.LogRecord("svc", logging.INFO, __file__, 1, "x", None, None)
        rec.request_id = "-"
        rec.weird = object()  # not JSON-serializable
        line = JsonFormatter().format(rec)
        payload = json.loads(line)
        # Should fall back to repr() rather than crash
        self.assertIn("object", payload["weird"])


# -----------------------------------------------------------------------------
# 3-6. Request ID middleware
# -----------------------------------------------------------------------------
class RequestIdMiddlewareTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        os.environ.pop("LINE_CHANNEL_SECRET", None)
        os.environ.pop("DIALOGFLOW_WEBHOOK_TOKEN", None)
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client()

    def test_response_includes_x_request_id_when_none_inbound(self):
        client = self._build_client()
        resp = client.get("/healthz")
        self.assertIn("X-Request-ID", resp.headers)
        rid = resp.headers["X-Request-ID"]
        # UUID4 hex = 32 chars
        self.assertEqual(len(rid), 32)

    def test_inbound_x_request_id_is_echoed(self):
        client = self._build_client()
        resp = client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
        self.assertEqual(resp.headers["X-Request-ID"], "trace-abc-123")

    def test_render_request_id_is_used_as_fallback(self):
        client = self._build_client()
        resp = client.get(
            "/healthz",
            headers={"Render-Request-Id": "render-456"},
        )
        self.assertEqual(resp.headers["X-Request-ID"], "render-456")

    def test_x_request_id_takes_precedence_over_render_header(self):
        client = self._build_client()
        resp = client.get("/healthz", headers={
            "X-Request-ID": "preferred",
            "Render-Request-Id": "ignored",
        })
        self.assertEqual(resp.headers["X-Request-ID"], "preferred")

    def test_inbound_request_id_is_capped(self):
        client = self._build_client()
        long_id = "x" * 500
        resp = client.get("/healthz", headers={"X-Request-ID": long_id})
        self.assertLessEqual(len(resp.headers["X-Request-ID"]), 64)


# -----------------------------------------------------------------------------
# 7-9. Error metrics on /webhook
# -----------------------------------------------------------------------------
class WebhookErrorMetricsTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        os.environ.pop("DIALOGFLOW_WEBHOOK_TOKEN", None)
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client()

    def _df_payload(self, intent="GetKnowledge"):
        return {
            "queryResult": {
                "intent": {"displayName": intent},
                "parameters": {},
                "queryText": "ความรู้",
            },
            "session": "projects/x/agent/sessions/U-test-1",
        }

    def test_intent_counter_increments_on_success(self):
        client = self._build_client()
        resp = client.post("/webhook", json=self._df_payload("GetKnowledge"))
        self.assertEqual(resp.status_code, 200)

        from services.metrics import snapshot
        snap = snapshot()
        self.assertGreaterEqual(snap.get("webhook.intent.GetKnowledge", 0), 1)
        self.assertEqual(snap.get("webhook.error.GetKnowledge", 0), 0)

    def test_unknown_intent_counted_under_unknown_label(self):
        client = self._build_client()
        resp = client.post("/webhook", json=self._df_payload("SomeNewIntent"))
        self.assertEqual(resp.status_code, 200)
        from services.metrics import snapshot
        self.assertGreaterEqual(
            snapshot().get("webhook.intent.SomeNewIntent", 0), 1,
        )

    def test_handler_exception_increments_error_counter(self):
        client = self._build_client()

        # Force handle_get_knowledge to raise
        from routes import webhook as wh
        with patch.object(wh, "handle_get_knowledge", side_effect=RuntimeError("boom")):
            resp = client.post("/webhook", json=self._df_payload("GetKnowledge"))

        # User still gets a graceful 200 + Thai error message
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("ขัดข้อง", body.get("fulfillmentText", ""))

        from services.metrics import snapshot
        snap = snapshot()
        self.assertGreaterEqual(snap.get("webhook.error.GetKnowledge", 0), 1)
        # intent counter should ALSO be incremented (we count attempts)
        self.assertGreaterEqual(snap.get("webhook.intent.GetKnowledge", 0), 1)

    def test_intent_label_sanitized(self):
        """Intent names with dots should not break metric key naming."""
        client = self._build_client()
        client.post("/webhook", json=self._df_payload("Some.Weird.Name"))
        from services.metrics import snapshot
        # Dots replaced with underscores
        self.assertGreaterEqual(
            snapshot().get("webhook.intent.Some_Weird_Name", 0), 1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
