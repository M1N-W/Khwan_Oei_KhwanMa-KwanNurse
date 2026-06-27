# -*- coding: utf-8 -*-
"""
Tests: Personalized Education Delivery via LINE Flex Carousels (Sprint 2 Phase 3).
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
# T4 — Education Carousel Flex Message Builder
# ---------------------------------------------------------------------------
class TestEducationCarousel(unittest.TestCase):
    """build_education_carousel() returns a micro-bubble Flex carousel of guides."""

    _RECS = [
        {"key": "wound_care", "title": "การดูแลแผล",
         "reason": "หลังผ่าตัดทุกรายควรรู้", "source": "rule"},
        {"key": "dvt_prevention", "title": "ป้องกันลิ่มเลือด",
         "reason": "ความเสี่ยงหลังผ่าตัด", "source": "rule"},
        {"key": "medication", "title": "การรับประทานยา",
         "reason": "มีโรคประจำตัว", "source": "llm"},
    ]

    def _get(self, recs):
        from services.line_message import build_education_carousel
        return build_education_carousel(recs)

    def test_returns_flex_carousel_type(self):
        msg = self._get(self._RECS)
        self.assertEqual(msg["type"], "flex")
        self.assertEqual(msg["contents"]["type"], "carousel")

    def test_carousel_has_correct_bubble_count(self):
        msg = self._get(self._RECS)
        self.assertEqual(len(msg["contents"]["contents"]), 3)

    def test_each_bubble_contains_guide_title(self):
        msg = self._get(self._RECS)
        all_text = str(msg)
        for rec in self._RECS:
            self.assertIn(rec["title"], all_text)

    def test_reason_appears_in_carousel(self):
        msg = self._get(self._RECS)
        self.assertIn("หลังผ่าตัดทุกรายควรรู้", str(msg))

    def test_empty_recommendations_returns_text_fallback(self):
        msg = self._get([])
        self.assertEqual(msg["type"], "text")
        self.assertIn("ไม่พบ", msg["text"])

    def test_alttext_contains_guide_count(self):
        msg = self._get(self._RECS)
        self.assertIn("3", msg["altText"])

    def test_alttext_under_400_chars(self):
        msg = self._get(self._RECS)
        self.assertLessEqual(len(msg["altText"]), 400)


# ---------------------------------------------------------------------------
# T5 — RecommendKnowledge Intent Integration
# ---------------------------------------------------------------------------
class TestRecommendKnowledgeUsesCarousel(unittest.TestCase):
    """RecommendKnowledge webhook intent returns the Flex carousel."""

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-recommend-knowledge")
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _df_payload(self, params=None, user_id="U-edu-test-12345"):
        return {
            "queryResult": {
                "intent": {"displayName": "RecommendKnowledge"},
                "parameters": params or {},
                "queryText": "ขอคำแนะนำหน่อย",
            },
            "session": f"projects/x/agent/sessions/{user_id}",
        }

    def test_recommend_knowledge_sends_flex_carousel(self):
        with patch("services.line_message.push_rich_message") as mock_push, \
             patch("database.patient_profile.read_patient_profile", return_value=None), \
             patch("config.ENABLE_RICH_MESSAGES", True):
            resp = self.client.post(
                "/webhook",
                json=self._df_payload({"surgery_type": "knee_replacement"}),
            )
        self.assertEqual(resp.status_code, 200)
        # Push should be called with the Flex carousel
        mock_push.assert_called_once()
        flex_msg = mock_push.call_args[0][0]
        # First argument should be list containing flex carousel
        self.assertEqual(flex_msg[0]["type"], "flex")
        self.assertEqual(flex_msg[0]["contents"]["type"], "carousel")


# ---------------------------------------------------------------------------
# T6 — Proactive Education after ReportSymptoms
# ---------------------------------------------------------------------------
class TestProactiveEducationAfterSymptoms(unittest.TestCase):
    """ReportSymptoms webhook intent proactively pushes education carousel."""

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-proactive-education")
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _df_payload(self, user_id="U-proactive-test-12345"):
        return {
            "queryResult": {
                "intent": {"displayName": "ReportSymptoms"},
                "parameters": {
                    "pain_score": 2,
                    "wound_status": "ปกติ",
                    "fever_check": "ไม่มี",
                    "mobility_status": "เดินได้ปกติ",
                },
                "session": f"projects/x/agent/sessions/{user_id}",
            }
        }

    def test_report_symptoms_triggers_education_carousel_push(self):
        stored_profile = {
            "age": 62,
            "sex": "m",
            "surgery_type": "knee_replacement",
            "diseases": ["เบาหวาน"],
        }
        with patch("services.line_message.push_rich_message") as mock_push, \
             patch("database.patient_profile.read_patient_profile", return_value=stored_profile), \
             patch("config.ENABLE_RICH_MESSAGES", True):
            resp = self.client.post("/webhook", json=self._df_payload())
        self.assertEqual(resp.status_code, 200)
        # Verify that push_rich_message was called to send the carousel
        all_calls = mock_push.call_args_list
        carousel_pushed = any("carousel" in str(c) for c in all_calls)
        self.assertTrue(carousel_pushed, "Education carousel was not pushed after ReportSymptoms")


if __name__ == "__main__":
    unittest.main()
