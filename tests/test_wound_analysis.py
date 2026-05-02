# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 2 (S2-2): ทดสอบ wound image analysis pipeline.

ขอบเขต:
- ``services.wound_analysis.analyze_wound_image``: success/skip cases,
  normalization, oversize rejection, LLM disabled, invalid response.
- ``services.notification`` helpers: ``download_line_content``,
  ``reply_line_message``, ``build_wound_alert_message``,
  ``build_wound_user_reply``.
- ``database.wound_logs.save_wound_analysis``: append + parse roundtrip.
- LINE webhook route ``/line/webhook``: 200 always, image events trigger
  the orchestrator, non-image events are ignored.

Run::

    python -m unittest test_wound_analysis.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


# Small helper to make a fake JPEG-ish payload (header is enough for our tests)
_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 1024  # ~1KB


# -----------------------------------------------------------------------------
# wound_analysis.analyze_wound_image
# -----------------------------------------------------------------------------
class AnalyzeWoundImageTests(unittest.TestCase):

    def test_empty_image_returns_none(self):
        from services.wound_analysis import analyze_wound_image
        self.assertIsNone(analyze_wound_image(b""))
        self.assertIsNone(analyze_wound_image(None))

    def test_oversize_image_rejected(self):
        from services import wound_analysis
        from config import LLM_VISION_MAX_IMAGE_BYTES
        big = b"x" * (LLM_VISION_MAX_IMAGE_BYTES + 1)
        self.assertIsNone(wound_analysis.analyze_wound_image(big))

    def test_llm_disabled_returns_none(self):
        from services import wound_analysis
        with patch.object(wound_analysis.llm_module, "is_enabled", return_value=False):
            self.assertIsNone(wound_analysis.analyze_wound_image(_FAKE_JPEG))

    def test_llm_returns_none_propagates(self):
        from services import wound_analysis
        with patch.object(wound_analysis.llm_module, "is_enabled", return_value=True), \
             patch.object(wound_analysis.llm_module, "complete_image_json",
                          return_value=None):
            self.assertIsNone(wound_analysis.analyze_wound_image(_FAKE_JPEG))

    def test_happy_path_returns_normalized_dict(self):
        from services import wound_analysis
        raw = {
            "severity": "high",
            "observations": ["บวมแดง", "มีหนองเล็กน้อย", " ", ""],  # blanks dropped
            "advice": "ทำแผลและพบพยาบาล หากอาการแย่ลงให้ติดต่อพยาบาล",
            "confidence": 0.85,
        }
        with patch.object(wound_analysis.llm_module, "is_enabled", return_value=True), \
             patch.object(wound_analysis.llm_module, "complete_image_json",
                          return_value=raw):
            result = wound_analysis.analyze_wound_image(_FAKE_JPEG)
        self.assertEqual(result["severity"], "high")
        self.assertEqual(result["observations"], ["บวมแดง", "มีหนองเล็กน้อย"])
        self.assertEqual(result["confidence"], 0.85)
        self.assertIn("พยาบาล", result["advice"])

    def test_invalid_severity_coerced_to_medium(self):
        from services import wound_analysis
        raw = {"severity": "EXTREME", "observations": ["x"], "advice": "y", "confidence": 0.4}
        with patch.object(wound_analysis.llm_module, "is_enabled", return_value=True), \
             patch.object(wound_analysis.llm_module, "complete_image_json", return_value=raw):
            result = wound_analysis.analyze_wound_image(_FAKE_JPEG)
        self.assertEqual(result["severity"], "medium")

    def test_confidence_clamped_to_unit_interval(self):
        from services import wound_analysis
        raw_hi = {"severity": "low", "observations": [], "advice": "", "confidence": 5.5}
        raw_lo = {"severity": "low", "observations": [], "advice": "", "confidence": -0.3}
        with patch.object(wound_analysis.llm_module, "is_enabled", return_value=True), \
             patch.object(wound_analysis.llm_module, "complete_image_json",
                          side_effect=[raw_hi, raw_lo]):
            r1 = wound_analysis.analyze_wound_image(_FAKE_JPEG)
            r2 = wound_analysis.analyze_wound_image(_FAKE_JPEG)
        self.assertEqual(r1["confidence"], 1.0)
        self.assertEqual(r2["confidence"], 0.0)

    def test_non_dict_response_returns_none(self):
        from services import wound_analysis
        with patch.object(wound_analysis.llm_module, "is_enabled", return_value=True), \
             patch.object(wound_analysis.llm_module, "complete_image_json",
                          return_value=["not", "a", "dict"]):
            self.assertIsNone(wound_analysis.analyze_wound_image(_FAKE_JPEG))


# -----------------------------------------------------------------------------
# notification helpers (LINE Content + Reply + alert builders)
# -----------------------------------------------------------------------------
class NotificationHelpersTests(unittest.TestCase):

    def setUp(self):
        # ทำให้ token มีค่าใน test (config โหลดเป็น None ปกติ)
        from services import notification
        self._patcher_token = patch.object(notification, "LINE_CHANNEL_ACCESS_TOKEN", "fake-token")
        self._patcher_token.start()

    def tearDown(self):
        self._patcher_token.stop()

    def test_download_line_content_success(self):
        from services import notification

        class _Resp:
            status_code = 200
            content = b"image-bytes"

        with patch.object(notification.requests, "get", return_value=_Resp()):
            data = notification.download_line_content("MSG-1")
        self.assertEqual(data, b"image-bytes")

    def test_download_line_content_4xx_returns_none(self):
        from services import notification

        class _Resp:
            status_code = 404
            content = b""

        with patch.object(notification.requests, "get", return_value=_Resp()):
            self.assertIsNone(notification.download_line_content("MSG-1"))

    def test_download_line_content_timeout_returns_none(self):
        from services import notification
        with patch.object(notification.requests, "get",
                          side_effect=notification.requests.exceptions.Timeout):
            self.assertIsNone(notification.download_line_content("MSG-1"))

    def test_download_line_content_empty_message_id(self):
        from services import notification
        self.assertIsNone(notification.download_line_content(""))

    def test_reply_line_message_success(self):
        from services import notification

        class _Resp:
            status_code = 200
            text = ""

        with patch.object(notification.requests, "post", return_value=_Resp()):
            self.assertTrue(notification.reply_line_message("REPLY-X", "hello"))

    def test_reply_line_message_missing_token_or_text(self):
        from services import notification
        self.assertFalse(notification.reply_line_message("", "x"))
        self.assertFalse(notification.reply_line_message("R", ""))

    def test_build_wound_alert_message_includes_severity_and_obs(self):
        from services.notification import build_wound_alert_message
        msg = build_wound_alert_message(
            user_id="U-abc",
            severity="high",
            observations=["บวม", "แดง"],
            advice="พบพยาบาลด่วน",
            confidence=0.9,
        )
        self.assertIn("U-abc", msg)
        self.assertIn("สูง", msg)        # Thai severity label
        self.assertIn("90%", msg)        # confidence pct
        self.assertIn("บวม", msg)
        self.assertIn("พบพยาบาลด่วน", msg)

    def test_build_wound_user_reply_high_includes_warning(self):
        from services.notification import build_wound_user_reply
        msg = build_wound_user_reply("high", ["บวม"], "ติดต่อพยาบาล")
        self.assertIn("AI", msg)
        self.assertIn("พยาบาล", msg)
        self.assertIn("สูง", msg)


# -----------------------------------------------------------------------------
# database/wound_logs persistence
# -----------------------------------------------------------------------------
class WoundLogsTests(unittest.TestCase):

    def test_save_appends_row_with_expected_shape(self):
        from database import wound_logs

        captured = {}

        class _Sheet:
            def append_row(self, row, value_input_option=None):
                captured["row"] = row
                captured["opt"] = value_input_option

        with patch.object(wound_logs, "get_worksheet", return_value=_Sheet()):
            ok = wound_logs.save_wound_analysis(
                user_id="U-x",
                severity="medium",
                observations=["บวม", "แดง"],
                advice="พบพยาบาล",
                confidence=0.72,
                image_size_kb=128,
                message_id="MSG-1",
            )
        self.assertTrue(ok)
        row = captured["row"]
        self.assertEqual(len(row), 8)
        self.assertEqual(row[1], "U-x")
        self.assertEqual(row[2], "medium")
        self.assertIn("บวม", row[3])
        self.assertIn("แดง", row[3])
        self.assertEqual(row[4], "พบพยาบาล")
        self.assertEqual(row[5], "0.72")
        self.assertEqual(row[6], "128")
        self.assertEqual(row[7], "MSG-1")

    def test_save_returns_false_when_sheet_unavailable(self):
        from database import wound_logs
        with patch.object(wound_logs, "get_worksheet", return_value=None):
            self.assertFalse(wound_logs.save_wound_analysis(
                "U-x", "low", [], "", 0.5, 10,
            ))

    def test_observations_join_parse_roundtrip(self):
        from database.wound_logs import _join_observations, parse_observations
        original = ["บวม", "แดง", "หนอง"]
        encoded = _join_observations(original)
        decoded = parse_observations(encoded)
        self.assertEqual(decoded, original)


# -----------------------------------------------------------------------------
# /line/webhook route (smoke)
# -----------------------------------------------------------------------------
class LineWebhookRouteTests(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-for-line-webhook-tests")
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_empty_body_returns_200(self):
        resp = self.client.post("/line/webhook", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["events_received"], 0)

    def test_text_event_is_ignored(self):
        from routes import webhook as webhook_module
        with patch.object(webhook_module, "handle_line_image_event") as mock_handler:
            resp = self.client.post("/line/webhook", json={
                "events": [
                    {
                        "type": "message",
                        "replyToken": "rt-1",
                        "source": {"userId": "U-x"},
                        "message": {"type": "text", "id": "M-1", "text": "hello"},
                    }
                ]
            })
        self.assertEqual(resp.status_code, 200)
        mock_handler.assert_not_called()

    def test_image_event_calls_handler(self):
        from routes import webhook as webhook_module
        with patch.object(webhook_module, "handle_line_image_event") as mock_handler:
            resp = self.client.post("/line/webhook", json={
                "events": [
                    {
                        "type": "message",
                        "replyToken": "rt-1",
                        "source": {"userId": "U-x"},
                        "message": {"type": "image", "id": "M-img-1"},
                    }
                ]
            })
        self.assertEqual(resp.status_code, 200)
        mock_handler.assert_called_once()
        passed_event = mock_handler.call_args[0][0]
        self.assertEqual(passed_event["message"]["id"], "M-img-1")

    def test_handler_exception_does_not_break_route(self):
        from routes import webhook as webhook_module
        with patch.object(webhook_module, "handle_line_image_event",
                          side_effect=RuntimeError("boom")):
            resp = self.client.post("/line/webhook", json={
                "events": [{
                    "type": "message",
                    "message": {"type": "image", "id": "M-1"},
                }]
            })
        self.assertEqual(resp.status_code, 200)


# -----------------------------------------------------------------------------
# handle_line_image_event orchestrator (end-to-end with mocks)
# -----------------------------------------------------------------------------
class ImageEventOrchestratorTests(unittest.TestCase):

    def _event(self, message_id="M-1"):
        return {
            "type": "message",
            "replyToken": "RT-1",
            "source": {"userId": "U-orch-test-12345"},
            "message": {"type": "image", "id": message_id},
        }

    def test_no_message_id_replies_with_error(self):
        from routes import webhook as webhook_module
        with patch("services.notification.reply_line_message") as mock_reply:
            ev = self._event(message_id="")
            webhook_module.handle_line_image_event(ev)
        mock_reply.assert_called_once()
        self.assertIn("รหัสรูป", mock_reply.call_args[0][1])

    def test_download_failure_replies_friendly_error(self):
        from routes import webhook as webhook_module
        with patch("services.notification.download_line_content", return_value=None), \
             patch("services.notification.reply_line_message") as mock_reply:
            webhook_module.handle_line_image_event(self._event())
        mock_reply.assert_called_once()
        self.assertIn("ดาวน์โหลด", mock_reply.call_args[0][1])

    def test_llm_disabled_falls_back_to_raw_nurse_notice(self):
        from routes import webhook as webhook_module
        with patch("services.notification.download_line_content", return_value=_FAKE_JPEG), \
             patch("services.wound_analysis.analyze_wound_image", return_value=None), \
             patch("services.notification.reply_line_message") as mock_reply, \
             patch("services.notification.send_line_push") as mock_push, \
             patch.object(webhook_module, "NURSE_GROUP_ID", "GROUP-X"):
            webhook_module.handle_line_image_event(self._event())
        mock_reply.assert_called_once()
        mock_push.assert_called_once()  # raw nurse notice still goes out

    def test_high_severity_triggers_save_reply_and_alert(self):
        from routes import webhook as webhook_module
        analysis = {
            "severity": "high",
            "observations": ["บวมแดง"],
            "advice": "พบพยาบาล",
            "confidence": 0.9,
        }
        with patch("services.notification.download_line_content", return_value=_FAKE_JPEG), \
             patch("services.wound_analysis.analyze_wound_image", return_value=analysis), \
             patch("database.wound_logs.save_wound_analysis", return_value=True) as mock_save, \
             patch("services.notification.reply_line_message") as mock_reply, \
             patch("services.notification.send_line_push") as mock_push, \
             patch.object(webhook_module, "NURSE_GROUP_ID", "GROUP-X"):
            webhook_module.handle_line_image_event(self._event())
        mock_save.assert_called_once()
        mock_reply.assert_called_once()
        mock_push.assert_called_once()
        # Nurse alert message contains severity label
        self.assertIn("สูง", mock_push.call_args[0][0])

    def test_low_severity_does_not_alert_nurse(self):
        from routes import webhook as webhook_module
        analysis = {
            "severity": "low",
            "observations": [],
            "advice": "ดูแลตามปกติ",
            "confidence": 0.95,
        }
        with patch("services.notification.download_line_content", return_value=_FAKE_JPEG), \
             patch("services.wound_analysis.analyze_wound_image", return_value=analysis), \
             patch("database.wound_logs.save_wound_analysis", return_value=True), \
             patch("services.notification.reply_line_message") as mock_reply, \
             patch("services.notification.send_line_push") as mock_push, \
             patch.object(webhook_module, "NURSE_GROUP_ID", "GROUP-X"):
            webhook_module.handle_line_image_event(self._event())
        mock_reply.assert_called_once()
        mock_push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
