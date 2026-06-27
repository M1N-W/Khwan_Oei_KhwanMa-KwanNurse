# -*- coding: utf-8 -*-
"""
Tests: Wound Vision UX — photography guide & Flex result builders (Sprint 2 Phase 3).
All builders are pure functions (no HTTP, no side-effects); tests are pure unit tests.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
os.environ.setdefault("NURSE_GROUP_ID", "test_nurse_group")


# ---------------------------------------------------------------------------
# T1 — Photography Guide Flex Message
# ---------------------------------------------------------------------------
class TestWoundPhotographyGuide(unittest.TestCase):
    """build_wound_photography_guide() returns a valid Flex bubble with UX tips."""

    def _get(self):
        from services.line_message import build_wound_photography_guide
        return build_wound_photography_guide()

    def test_returns_flex_type(self):
        msg = self._get()
        self.assertEqual(msg["type"], "flex")

    def test_alttext_is_correct(self):
        msg = self._get()
        self.assertEqual(msg["altText"], "วิธีถ่ายภาพแผล")

    def test_contains_lighting_tip(self):
        msg = self._get()
        self.assertIn("แสงธรรมชาติ", str(msg))

    def test_contains_scale_reference_tip(self):
        msg = self._get()
        self.assertIn("เหรียญ", str(msg))

    def test_has_cta_button_with_camera_label(self):
        msg = self._get()
        self.assertIn("ส่งรูปแผล", str(msg))

    def test_has_header_and_body_and_footer(self):
        bubble = self._get()["contents"]
        self.assertIn("header", bubble)
        self.assertIn("body", bubble)
        self.assertIn("footer", bubble)

    def test_header_has_blue_background(self):
        msg = self._get()
        # Blue header signals medical/informational context
        self.assertIn("#1565C0", str(msg))

    def test_alttext_under_400_chars(self):
        """LINE Flex altText limit is 400 chars."""
        msg = self._get()
        self.assertLessEqual(len(msg["altText"]), 400)


# ---------------------------------------------------------------------------
# T2 — Wound Result Flex Message
# ---------------------------------------------------------------------------
class TestWoundFlexResult(unittest.TestCase):
    """build_wound_flex_result() returns a color-coded Flex bubble."""

    def _get(self, severity="medium", obs=None, advice="แจ้งพยาบาล", conf=0.75):
        from services.line_message import build_wound_flex_result
        return build_wound_flex_result(severity, ["บวมแดง"] if obs is None else obs, advice, conf)

    def test_flex_type(self):
        self.assertEqual(self._get()["type"], "flex")

    def test_alttext_contains_word_wound(self):
        msg = self._get()
        self.assertIn("แผล", msg["altText"])

    def test_high_severity_uses_red_header(self):
        msg = self._get(severity="high")
        self.assertIn("#C62828", str(msg))

    def test_medium_severity_uses_orange_header(self):
        msg = self._get(severity="medium")
        self.assertIn("#EF6C00", str(msg))

    def test_low_severity_uses_green_header(self):
        msg = self._get(severity="low", obs=[], advice="ดูแลตามปกติ", conf=0.95)
        self.assertIn("#2E7D32", str(msg))

    def test_observations_appear_in_body(self):
        msg = self._get(obs=["บวม", "มีน้ำเหลือง"])
        body_str = str(msg)
        self.assertIn("บวม", body_str)
        self.assertIn("มีน้ำเหลือง", body_str)

    def test_empty_observations_shows_fallback(self):
        msg = self._get(obs=[])
        # Should not crash; should show a "no obvious findings" note
        self.assertEqual(msg["type"], "flex")
        self.assertIn("ไม่พบ", str(msg))

    def test_confidence_percentage_in_header(self):
        msg = self._get(conf=0.82)
        # 82% should appear somewhere in the header
        self.assertIn("82%", str(msg))

    def test_high_severity_shows_nurse_notice(self):
        msg = self._get(severity="high")
        self.assertIn("พยาบาล", str(msg))

    def test_low_severity_no_nurse_notice_in_header(self):
        msg = self._get(severity="low", obs=[], advice="ดูแลต่อ", conf=0.9)
        # Low severity should not alarm the patient with a nurse warning
        body_str = str(msg["contents"].get("body", {}))
        self.assertNotIn("⚠️ พยาบาลจะได้รับการแจ้งเตือน", body_str)

    def test_unknown_severity_coerced_to_medium(self):
        """Invalid severity must not crash — coerce to medium (safe default)."""
        from services.line_message import build_wound_flex_result
        msg = build_wound_flex_result("extreme", [], "ตรวจสอบ", 0.5)
        self.assertEqual(msg["type"], "flex")

    def test_advice_appears_in_body(self):
        msg = self._get(advice="ล้างแผลด้วยน้ำสะอาดวันละ 2 ครั้ง")
        self.assertIn("ล้างแผลด้วยน้ำสะอาดวันละ 2 ครั้ง", str(msg))

    def test_has_contact_nurse_button(self):
        msg = self._get()
        self.assertIn("ติดต่อพยาบาล", str(msg))

    def test_alttext_under_400_chars(self):
        self.assertLessEqual(len(self._get()["altText"]), 400)


# ---------------------------------------------------------------------------
# T3 — Photography Guide Trigger (route-level integration)
# ---------------------------------------------------------------------------
class TestPhotographyGuideTrigger(unittest.TestCase):
    """After ReportSymptoms with wound keywords → photography guide is pushed."""

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-wound-guide-trigger")
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _dialogflow_payload(self, wound_text, pain=3, user_id="U-wound-guide-test-12345"):
        return {
            "queryResult": {
                "intent": {"displayName": "ReportSymptoms"},
                "parameters": {
                    "pain_score": pain,
                    "wound_status": wound_text,
                    "fever_check": "ไม่มี",
                    "mobility_status": "เดินได้",
                },
                "outputContexts": [{
                    "name": f"projects/x/sessions/s/contexts/c",
                    "parameters": {"line_user_id": user_id},
                }],
            }
        }

    def test_wound_keyword_hnung_triggers_guide(self):
        """หนอง (pus) is a high-signal wound keyword."""
        with patch("services.line_message.push_rich_message") as mock_push:
            resp = self.client.post(
                "/dialogflow/webhook",
                json=self._dialogflow_payload("แผลมีหนองและบวมแดง"),
            )
        self.assertEqual(resp.status_code, 200)
        # Photography guide (altText = "วิธีถ่ายภาพแผล") must have been pushed
        all_calls_str = str(mock_push.call_args_list)
        self.assertIn("วิธีถ่ายภาพแผล", all_calls_str)

    def test_wound_keyword_buam_triggers_guide(self):
        """บวม (swollen) is a wound keyword."""
        with patch("services.line_message.push_rich_message") as mock_push:
            resp = self.client.post(
                "/dialogflow/webhook",
                json=self._dialogflow_payload("บวมรอบแผล"),
            )
        self.assertEqual(resp.status_code, 200)
        all_calls_str = str(mock_push.call_args_list)
        self.assertIn("วิธีถ่ายภาพแผล", all_calls_str)

    def test_non_wound_symptom_does_not_trigger_guide(self):
        """Pain-only report with normal wound should NOT trigger the guide."""
        with patch("services.line_message.push_rich_message") as mock_push:
            resp = self.client.post(
                "/dialogflow/webhook",
                json=self._dialogflow_payload("ปกติ แห้ง"),  # normal wound
            )
        self.assertEqual(resp.status_code, 200)
        all_calls_str = str(mock_push.call_args_list)
        self.assertNotIn("วิธีถ่ายภาพแผล", all_calls_str)

    def test_guide_trigger_does_not_break_normal_response(self):
        """Even with guide push, the Dialogflow reply must still be returned."""
        with patch("services.line_message.push_rich_message"):
            resp = self.client.post(
                "/dialogflow/webhook",
                json=self._dialogflow_payload("หนอง"),
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("fulfillmentText", data)


if __name__ == "__main__":
    unittest.main()
