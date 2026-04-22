# -*- coding: utf-8 -*-
"""
Phase 2 regression tests: LLM seam, PII scrubber, NLP triage, education recommender.

Run: python -m unittest test_llm.py -v
These tests DO NOT hit any external API. The Gemini HTTP call is fully mocked.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# UTF-8 safe + repo-local imports
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


# ===========================================================================
# PII scrubber
# ===========================================================================
class PiiScrubberTests(unittest.TestCase):
    def test_scrubs_thai_phone(self):
        from utils.pii import scrub_pii
        text = "ติดต่อ 0812345678 หรือ 081-234-5678"
        out = scrub_pii(text)
        self.assertNotIn("0812345678", out)
        self.assertIn("[PHONE]", out)

    def test_scrubs_line_id(self):
        from utils.pii import scrub_pii
        line_id = "U" + "a" * 32
        out = scrub_pii(f"user {line_id} reported pain")
        self.assertIn("[LINE_ID]", out)
        self.assertNotIn(line_id, out)

    def test_scrubs_national_id(self):
        from utils.pii import scrub_pii
        out = scrub_pii("บัตรประชาชน 1234567890123")
        self.assertIn("[NATIONAL_ID]", out)

    def test_scrubs_email(self):
        from utils.pii import scrub_pii
        out = scrub_pii("ติดต่อ patient@example.com")
        self.assertIn("[EMAIL]", out)

    def test_handles_non_string_and_none(self):
        from utils.pii import scrub_pii
        self.assertIsNone(scrub_pii(None))
        self.assertEqual(scrub_pii(""), "")

    def test_scrub_user_id_masks_middle(self):
        from utils.pii import scrub_user_id
        self.assertEqual(scrub_user_id("Uabcdef1234567890"), "Uabc***7890")
        self.assertEqual(scrub_user_id("abc"), "***")
        self.assertEqual(scrub_user_id(None), "[unknown]")


# ===========================================================================
# LLM provider (Gemini adapter)
# ===========================================================================
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


def _gemini_ok_payload(text):
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}}
        ]
    }


class LlmProviderTests(unittest.TestCase):
    def setUp(self):
        from services import llm
        llm._reset_state_for_tests()

    def test_disabled_when_provider_is_none(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services import llm as llm_mod
            self.assertFalse(llm_mod.is_enabled())
            self.assertIsNone(llm_mod.complete("sys", "hi"))

    def test_disabled_when_gemini_without_key(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", ""):
            from services import llm as llm_mod
            self.assertFalse(llm_mod.is_enabled())

    def test_gemini_happy_path_strips_json_fences(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(
                200, _gemini_ok_payload('```json\n{"risk_level":"low"}\n```'),
            )
            from services import llm as llm_mod
            parsed = llm_mod.complete_json("sys", "user")
            self.assertEqual(parsed, {"risk_level": "low"})
            # PII must have been scrubbed before the HTTP call
            args, kwargs = mock_post.call_args
            body = kwargs.get("json") or {}
            sent_text = body["contents"][0]["parts"][-1]["text"]
            self.assertEqual(sent_text, "user")

    def test_gemini_scrubs_pii_before_sending(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(200, _gemini_ok_payload("ok"))
            from services import llm as llm_mod
            llm_mod.complete("sys", "โทร 0812345678 เบอร์เดิม")
            body = mock_post.call_args.kwargs["json"]
            sent_text = body["contents"][0]["parts"][-1]["text"]
            self.assertIn("[PHONE]", sent_text)
            self.assertNotIn("0812345678", sent_text)

    def test_circuit_opens_after_consecutive_failures(self):
        import requests
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.LLM_CIRCUIT_FAILURE_THRESHOLD", 2), \
             patch("services.llm.LLM_CIRCUIT_COOLDOWN_SECONDS", 60), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout()
            from services import llm as llm_mod
            self.assertIsNone(llm_mod.complete("s", "u"))
            self.assertIsNone(llm_mod.complete("s", "u"))
            # circuit should be open now; a third call must skip HTTP entirely
            mock_post.reset_mock()
            mock_post.side_effect = None
            mock_post.return_value = _FakeResponse(200, _gemini_ok_payload("x"))
            self.assertIsNone(llm_mod.complete("s", "u"))
            mock_post.assert_not_called()

    def test_invalid_json_returns_none(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(200, _gemini_ok_payload("not json"))
            from services import llm as llm_mod
            self.assertIsNone(llm_mod.complete_json("s", "u"))


# ===========================================================================
# Free-text NLP triage
# ===========================================================================
class NlpTriageTests(unittest.TestCase):
    def test_rule_based_high_risk_on_pus(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.nlp import analyze_free_text
            res = analyze_free_text("แผลมีหนองและมีไข้")
            self.assertEqual(res["risk_level"], "high")
            self.assertIn("wound_pus", res["flags"])
            self.assertIn("fever", res["flags"])
            self.assertEqual(res["source"], "rule")

    def test_rule_based_low_risk_on_mild_text(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.nlp import analyze_free_text
            res = analyze_free_text("วันนี้รู้สึกดีขึ้นนิดหน่อย")
            self.assertEqual(res["risk_level"], "low")
            self.assertEqual(res["flags"], [])

    def test_llm_can_escalate_but_not_downgrade_rule_risk(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            # Rule-based alone would be 'high' due to หนอง + ไข้; LLM tries
            # to downgrade to 'low' — final level must stay 'high'.
            mock_post.return_value = _FakeResponse(
                200,
                _gemini_ok_payload('{"risk_level":"low","flags":[],"summary":"ปกติ"}'),
            )
            from services import llm as llm_mod
            llm_mod._reset_state_for_tests()
            from services.nlp import analyze_free_text
            res = analyze_free_text("แผลมีหนองและมีไข้")
            self.assertEqual(res["risk_level"], "high")
            self.assertEqual(res["source"], "merged")

    def test_empty_text_returns_low(self):
        from services.nlp import analyze_free_text
        res = analyze_free_text("")
        self.assertEqual(res["risk_level"], "low")

    def test_format_triage_message_includes_header(self):
        from services.nlp import format_triage_message
        msg = format_triage_message({
            "risk_level": "high",
            "flags": ["fever"],
            "summary": "มีไข้ต่อเนื่อง",
            "source": "rule",
        })
        self.assertIn("🚨", msg)
        self.assertIn("มีไข้ต่อเนื่อง", msg)


# ===========================================================================
# Personalized education recommender
# ===========================================================================
class EducationRecommenderTests(unittest.TestCase):
    def test_rule_based_prioritizes_wound_care_first(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.education import recommend_guides
            recs = recommend_guides({}, top_n=5)
            self.assertEqual(recs[0]["key"], "wound_care")

    def test_orthopedic_elderly_boosts_dvt_and_pt(self):
        with patch("services.llm.LLM_PROVIDER", "none"):
            from services.education import recommend_guides
            recs = recommend_guides({
                "age": 72,
                "surgery_type": "hip replacement",
                "diseases": ["เบาหวาน", "ความดัน"],
            }, top_n=5)
            keys = [r["key"] for r in recs]
            # wound_care stays #1; dvt + pt should be in top 4
            self.assertEqual(keys[0], "wound_care")
            self.assertIn("dvt_prevention", keys[:4])
            self.assertIn("physical_therapy", keys[:4])

    def test_llm_refinement_uses_valid_keys_only(self):
        with patch("services.llm.LLM_PROVIDER", "gemini"), \
             patch("services.llm.GEMINI_API_KEY", "test-key"), \
             patch("services.llm.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(
                200,
                _gemini_ok_payload(
                    '{"ranked":['
                    '{"key":"medication","reason":"หลายตัว"},'
                    '{"key":"wound_care","reason":"ต้องดูแลแผล"},'
                    '{"key":"bogus","reason":"ignore"}'
                    ']}'
                ),
            )
            from services import llm as llm_mod
            llm_mod._reset_state_for_tests()
            from services.education import recommend_guides
            recs = recommend_guides({"age": 80}, top_n=5)
            keys = [r["key"] for r in recs]
            self.assertEqual(keys[0], "medication")
            self.assertNotIn("bogus", keys)
            # All catalog keys covered even if LLM omitted some
            self.assertEqual(set(keys),
                             {"medication", "wound_care", "physical_therapy",
                              "dvt_prevention", "warning_signs"})

    def test_format_recommendations_message_lists_titles(self):
        from services.education import format_recommendations_message
        msg = format_recommendations_message([
            {"key": "wound_care", "title": "การดูแลแผล", "reason": "R1", "source": "rule"},
            {"key": "medication", "title": "การรับประทานยา", "reason": "R2", "source": "rule"},
        ])
        self.assertIn("การดูแลแผล", msg)
        self.assertIn("การรับประทานยา", msg)
        self.assertIn("R1", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
