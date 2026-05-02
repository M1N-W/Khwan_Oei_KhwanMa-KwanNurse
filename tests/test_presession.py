# -*- coding: utf-8 -*-
"""
Phase 2-B regression tests: pre-consult briefing.

Run: python -m unittest test_presession.py -v
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


class _FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


def _gemini_ok(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class PreConsultBriefingTests(unittest.TestCase):
    def test_fallback_briefing_contains_category_and_risk(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.presession import build_pre_consult_briefing
            out = build_pre_consult_briefing(
                "u1", "wound", "แผลมีหนองและมีไข้ ปวด 8/10",
            )
        self.assertIn("Pre-consult briefing", out)
        self.assertIn("Risk:", out)
        self.assertIn("high", out)
        self.assertIn("คำถามที่ควรถาม", out)

    def test_fallback_picks_questions_matching_flags(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.presession import build_pre_consult_briefing
            out = build_pre_consult_briefing("u1", "wound", "แผลมีหนอง")
        # Should ask about wound discharge when wound_pus flag fires
        self.assertIn("สิ่งคัดหลั่ง", out)

    def test_llm_briefing_used_when_enabled(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(
                200,
                _gemini_ok(
                    '{"summary":"คนไข้มีแผลบวมและมีไข้ต่อเนื่อง",'
                    '"questions":["อุณหภูมิสูงสุดวันนี้?","ทานยาลดไข้แล้วหรือยัง?"]}'
                ),
            )
            from services import llm as llm_mod
            llm_mod._reset_state_for_tests()
            from services.presession import build_pre_consult_briefing
            out = build_pre_consult_briefing(
                "u1", "fever", "มีไข้และแผลบวม",
            )
        self.assertIn("คนไข้มีแผลบวม", out)
        self.assertIn("อุณหภูมิสูงสุด", out)

    def test_empty_description_still_returns_non_empty_briefing(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.presession import build_pre_consult_briefing
            out = build_pre_consult_briefing("u1", "other", "")
        self.assertIn("Pre-consult briefing", out)
        # Should have generic fallback questions
        self.assertIn("อาการเริ่มตั้งแต่", out)

    def test_invalid_llm_json_falls_back_to_rule(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(200, _gemini_ok("not json"))
            from services import llm as llm_mod
            llm_mod._reset_state_for_tests()
            from services.presession import build_pre_consult_briefing
            out = build_pre_consult_briefing("u1", "wound", "แผลมีหนอง")
        # Still produces a briefing from fallback path
        self.assertIn("Pre-consult briefing", out)
        self.assertIn("สิ่งคัดหลั่ง", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
