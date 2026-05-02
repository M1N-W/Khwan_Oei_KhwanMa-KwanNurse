# -*- coding: utf-8 -*-
"""
Hotfixes shipped on 2026-04-26 after the bugfix PR went live.

**Issue A — API key leak in logs.** Production stack traces of
``RequestException`` showed the full Gemini URL including ``?key=AIza...``.
Fix: ``services.llm._redact_api_key`` masks the value before logging.

**Issue B — Bug #3: AfterHoursChoice during office hours.** Users in-hours
see a 5-item category menu (1=ฉุกเฉิน ... 5=อื่นๆ) but Dialogflow routes
bare "1"/"2" replies to the ``AfterHoursChoice`` intent which was trained
for the 2-item after-hours menu. The handler now detects in-hours and
re-routes to the category-choice flow.

Run::

    python -m unittest test_hotfix_logsec_and_choice.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# Issue A — API key redaction.
# -----------------------------------------------------------------------------
class ApiKeyRedactionTests(unittest.TestCase):

    def test_redact_strips_query_string_key(self):
        from services.llm import _redact_api_key
        msg = (
            "429 Client Error: Too Many Requests for url: "
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.0-flash:generateContent?key=AIzaSyDgNExC_SECRETKEY"
        )
        out = _redact_api_key(msg)
        self.assertNotIn("AIzaSyDgNExC_SECRETKEY", out)
        self.assertIn("?key=***", out)

    def test_redact_handles_amp_separator(self):
        from services.llm import _redact_api_key
        msg = "https://example.com/x?foo=1&key=SECRET&bar=2"
        out = _redact_api_key(msg)
        self.assertNotIn("SECRET", out)
        self.assertIn("&key=***", out)
        self.assertIn("foo=1", out)
        self.assertIn("bar=2", out)

    def test_redact_no_key_unchanged(self):
        from services.llm import _redact_api_key
        msg = "Connection refused: https://example.com/no-key-here"
        self.assertEqual(_redact_api_key(msg), msg)

    def test_redact_handles_none(self):
        from services.llm import _redact_api_key
        self.assertIsNone(_redact_api_key(None))

    def test_request_exception_logged_redacted(self):
        """End-to-end: simulate a 429 and assert API key is masked in logs."""
        from services import llm

        leaky_url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.0-flash:generateContent?key=AIzaSyLEAKED"
        )
        leaky_exc = requests.exceptions.HTTPError(
            f"429 Client Error: Too Many Requests for url: {leaky_url}",
        )

        with patch.object(llm, "is_enabled", return_value=True), \
             patch.object(llm, "_circuit_open", return_value=False), \
             patch.object(llm, "_try_consume_daily_quota", return_value=True), \
             patch.object(llm, "scrub_pii", side_effect=lambda x: x), \
             patch.object(llm, "LLM_PROVIDER", "gemini"), \
             patch.object(llm, "_call_gemini", side_effect=leaky_exc), \
             patch.object(llm.logger, "warning") as mock_warn:
            result = llm.complete("sys", "user")

        self.assertIsNone(result)
        # Find the network-error log line
        net_calls = [
            c for c in mock_warn.call_args_list
            if c.args and "LLM network error" in str(c.args[0])
        ]
        self.assertTrue(
            net_calls,
            f"Expected an 'LLM network error' warning. Got calls: "
            f"{mock_warn.call_args_list}",
        )
        # logger.warning("LLM network error: %s", redacted_msg)
        # → call.args = ("LLM network error: %s", redacted_msg)
        rendered = net_calls[0].args[0] % net_calls[0].args[1]
        self.assertNotIn("AIzaSyLEAKED", rendered)
        self.assertIn("key=***", rendered)


# -----------------------------------------------------------------------------
# Issue B — Bug #3: AfterHoursChoice routes correctly during office hours.
# -----------------------------------------------------------------------------
class AfterHoursChoiceRoutingTests(unittest.TestCase):

    def test_in_hours_digit_one_routes_to_emergency_category(self):
        """The exact production scenario from 2026-04-26 15:54 logs."""
        from services import teleconsult
        with patch.object(teleconsult, "is_office_hours", return_value=True), \
             patch.object(teleconsult, "parse_category_choice",
                          return_value="emergency") as mock_parse, \
             patch.object(teleconsult, "start_teleconsult",
                          return_value={"success": True, "message": "queued"}) as mock_start:
            result = teleconsult.handle_after_hours_choice("U-x", "1")

        mock_parse.assert_called_once_with("1")
        mock_start.assert_called_once_with("U-x", "emergency", "")
        self.assertEqual(result["message"], "queued")

    def test_in_hours_digit_three_routes_to_third_category(self):
        """3 = แผลผ่าตัด — was previously unreachable from this handler."""
        from services import teleconsult
        with patch.object(teleconsult, "is_office_hours", return_value=True), \
             patch.object(teleconsult, "parse_category_choice",
                          return_value="surgery_wound") as mock_parse, \
             patch.object(teleconsult, "start_teleconsult",
                          return_value={"success": True, "message": "ok"}) as mock_start:
            teleconsult.handle_after_hours_choice("U-x", "3")
        mock_start.assert_called_once_with("U-x", "surgery_wound", "")

    def test_after_hours_digit_one_still_emergency(self):
        """Backward compat: outside hours, "1" still escalates emergency."""
        from services import teleconsult
        with patch.object(teleconsult, "is_office_hours", return_value=False), \
             patch.object(teleconsult, "get_user_active_session",
                          return_value={"Description": "อาการแย่ลง"}), \
             patch.object(teleconsult, "handle_emergency",
                          return_value={"success": True, "message": "esc"}) as mock_emerg:
            result = teleconsult.handle_after_hours_choice("U-y", "1")
        mock_emerg.assert_called_once_with("U-y", "อาการแย่ลง")
        self.assertEqual(result["message"], "esc")

    def test_after_hours_digit_two_still_non_urgent(self):
        from services import teleconsult
        with patch.object(teleconsult, "is_office_hours", return_value=False), \
             patch.object(teleconsult, "get_user_active_session",
                          return_value={"Issue_Type": "medication",
                                        "Description": "ลืมกินยา"}), \
             patch.object(teleconsult, "send_line_push") as mock_push:
            result = teleconsult.handle_after_hours_choice("U-z", "2")
        self.assertIn("บันทึกคำขอ", result["message"])
        mock_push.assert_called_once()

    def test_in_hours_unparseable_falls_through_to_legacy(self):
        """If digit doesn't map to a category, fall through to legacy menu."""
        from services import teleconsult
        with patch.object(teleconsult, "is_office_hours", return_value=True), \
             patch.object(teleconsult, "parse_category_choice", return_value=None):
            result = teleconsult.handle_after_hours_choice("U-x", "99")
        # Legacy "please type 1 or 2" message
        self.assertIn("กรุณาพิมพ์หมายเลข 1 หรือ 2", result["message"])


# -----------------------------------------------------------------------------
# /webhook integration — end-to-end through AfterHoursChoice intent.
# -----------------------------------------------------------------------------
class WebhookAfterHoursIntegrationTests(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-hotfix-bug3")
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_webhook_routes_in_hours_one_to_category_emergency(self):
        from services import teleconsult
        with patch.object(teleconsult, "is_office_hours", return_value=True), \
             patch.object(teleconsult, "parse_category_choice",
                          return_value="emergency"), \
             patch.object(teleconsult, "start_teleconsult",
                          return_value={"success": True,
                                        "message": "🚨 ติดต่อพยาบาลฉุกเฉิน"}):
            resp = self.client.post("/webhook", json={
                "queryResult": {
                    "intent": {"displayName": "AfterHoursChoice"},
                    "parameters": {},
                    "queryText": "1",
                },
                "session": "projects/x/agent/sessions/U-hotfix-bug3-test",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ฉุกเฉิน", resp.get_json()["fulfillmentText"])


if __name__ == "__main__":
    unittest.main()
