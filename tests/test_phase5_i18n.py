# -*- coding: utf-8 -*-
"""
Phase 5 P5-3: TH/EN internationalization.

Coverage:
 1. detect_language: pure Thai → th
 2. detect_language: pure English → en
 3. detect_language: mixed (Thai chars present) → th
 4. detect_language: empty/None → th default
 5. detect_language: pure numeric/emoji → th default
 6. normalize_lang: case-insensitive, fallback to default
 7. t() returns Thai for known key
 8. t() returns English for known key
 9. t() formats placeholders
10. t() handles missing format args without crashing
11. t() falls back to Thai when EN translation missing
12. t() returns key for unknown lookup
13. format_triage_message: lang='en' returns English header
14. format_triage_message: lang='th' (default) preserves Thai output
15. format_triage_message: unknown lang falls back to Thai
16. /webhook FreeTextSymptom EN input → EN reply
17. /webhook FreeTextSymptom TH input → TH reply
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# 1-5. detect_language
# -----------------------------------------------------------------------------
class DetectLanguageTests(unittest.TestCase):

    def test_pure_thai(self):
        from services.i18n import detect_language
        self.assertEqual(detect_language("ฉันปวดหัว"), "th")

    def test_pure_english(self):
        from services.i18n import detect_language
        self.assertEqual(detect_language("I have a headache"), "en")

    def test_mixed_thai_wins(self):
        from services.i18n import detect_language
        self.assertEqual(detect_language("ปวด head 8/10"), "th")
        self.assertEqual(detect_language("My แผล hurts"), "th")

    def test_empty_falls_back_to_th(self):
        from services.i18n import detect_language
        self.assertEqual(detect_language(""), "th")
        self.assertEqual(detect_language(None), "th")

    def test_pure_numeric_or_emoji_falls_back_to_th(self):
        from services.i18n import detect_language
        self.assertEqual(detect_language("123 456"), "th")
        self.assertEqual(detect_language("🎉🎉"), "th")


# -----------------------------------------------------------------------------
# 6. normalize_lang
# -----------------------------------------------------------------------------
class NormalizeLangTests(unittest.TestCase):

    def test_normalize_cases(self):
        from services.i18n import normalize_lang
        self.assertEqual(normalize_lang("EN"), "en")
        self.assertEqual(normalize_lang("th"), "th")
        self.assertEqual(normalize_lang("TH-th"), "th")  # truncated to 2 chars
        self.assertEqual(normalize_lang("en-US"), "en")  # truncated
        self.assertEqual(normalize_lang(None), "th")
        self.assertEqual(normalize_lang(""), "th")
        self.assertEqual(normalize_lang("xx"), "th")     # unsupported → default
        self.assertEqual(normalize_lang(" en "), "en")   # whitespace stripped


# -----------------------------------------------------------------------------
# 7-12. t() lookup
# -----------------------------------------------------------------------------
class TranslateLookupTests(unittest.TestCase):

    def test_thai_lookup(self):
        from services.i18n import t
        msg = t("triage.high", "th", score=9)
        self.assertIn("ความเสี่ยงสูง", msg)
        self.assertIn("9/10", msg)

    def test_english_lookup(self):
        from services.i18n import t
        msg = t("triage.high", "en", score=9)
        self.assertIn("HIGH risk", msg)
        self.assertIn("9/10", msg)

    def test_format_substitution(self):
        from services.i18n import t
        msg = t("triage.flags_label", "en", flags="fever, cough")
        self.assertIn("fever, cough", msg)

    def test_missing_format_arg_doesnt_crash(self):
        from services.i18n import t
        # 'score' placeholder not provided
        msg = t("triage.high", "en")
        self.assertIsInstance(msg, str)
        self.assertGreater(len(msg), 5)
        # Template returned with placeholder still visible (defensive default)
        self.assertIn("{score}", msg)

    def test_unknown_lang_falls_back_to_thai(self):
        from services.i18n import t
        msg = t("triage.high", "fr", score=5)  # French unsupported
        self.assertIn("ความเสี่ยงสูง", msg)

    def test_unknown_key_returns_key_name(self):
        from services.i18n import t
        result = t("not.a.real.key", "en")
        self.assertEqual(result, "not.a.real.key")

    def test_all_keys_have_both_th_and_en(self):
        """Catalog hygiene: every key should have both translations."""
        from services.i18n import _TRANSLATIONS
        missing = [
            k for k, v in _TRANSLATIONS.items()
            if "th" not in v or "en" not in v
        ]
        self.assertEqual(missing, [], f"Keys missing translations: {missing}")


# -----------------------------------------------------------------------------
# 13-15. format_triage_message language switch
# -----------------------------------------------------------------------------
class FormatTriageLangTests(unittest.TestCase):

    def _result(self, level="high"):
        return {
            "risk_level": level,
            "risk_score": 9 if level == "high" else (5 if level == "medium" else 2),
            "flags": ["fever", "wound infection"],
            "summary": "Patient reports high pain and pus discharge",
        }

    def test_english_high_risk(self):
        from services.nlp import format_triage_message
        msg = format_triage_message(self._result("high"), lang="en")
        self.assertIn("High-risk symptoms", msg)
        self.assertIn("Detected:", msg)
        self.assertIn("Type 'nurse'", msg)
        self.assertIn("1669", msg)
        # Should NOT contain Thai header
        self.assertNotIn("เสี่ยงสูง", msg)

    def test_english_medium_risk(self):
        from services.nlp import format_triage_message
        msg = format_triage_message(self._result("medium"), lang="en")
        self.assertIn("monitoring", msg.lower())
        self.assertIn("Summary:", msg)

    def test_english_low_risk(self):
        from services.nlp import format_triage_message
        msg = format_triage_message(self._result("low"), lang="en")
        # The EN low-risk header phrasing is "No clear high-risk signals detected"
        self.assertIn("No clear", msg)
        self.assertIn("If unsure", msg)
        self.assertNotIn("ความเสี่ยง", msg)  # no Thai bleed-through

    def test_default_language_still_thai(self):
        from services.nlp import format_triage_message
        msg = format_triage_message(self._result("high"))  # no lang arg
        self.assertIn("เสี่ยงสูง", msg)
        self.assertNotIn("High-risk symptoms", msg)

    def test_unknown_lang_falls_back_to_thai(self):
        from services.nlp import format_triage_message
        msg = format_triage_message(self._result("high"), lang="ja")
        self.assertIn("เสี่ยงสูง", msg)


# -----------------------------------------------------------------------------
# 16-17. /webhook FreeTextSymptom integration
# -----------------------------------------------------------------------------
class WebhookFreeTextLangTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        os.environ.pop("DIALOGFLOW_WEBHOOK_TOKEN", None)
        os.environ.pop("LINE_CHANNEL_SECRET", None)
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client()

    def _df_payload(self, text):
        return {
            "queryResult": {
                "intent": {"displayName": "FreeTextSymptom"},
                "parameters": {"symptom_text": text},
                "queryText": text,
            },
            "session": "projects/x/agent/sessions/U-test-1",
        }

    def test_english_input_yields_english_reply(self):
        client = self._build_client()
        triage = {"risk_level": "medium", "risk_score": 5,
                  "flags": ["pain"], "summary": "moderate"}
        # Patch at webhook module bind point — `from services.nlp import
        # analyze_free_text` binds the symbol there at import time.
        with patch("routes.webhook.analyze_free_text", return_value=triage):
            resp = client.post(
                "/webhook",
                json=self._df_payload("My wound is swollen and painful"),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json().get("fulfillmentText", "")
        self.assertIn("Recommend", body)  # EN header
        self.assertIn("monitoring", body.lower())
        self.assertNotIn("เสี่ยง", body)

    def test_thai_input_yields_thai_reply(self):
        client = self._build_client()
        triage = {"risk_level": "medium", "risk_score": 5,
                  "flags": ["ปวด"], "summary": "อาการปานกลาง"}
        with patch("routes.webhook.analyze_free_text", return_value=triage):
            resp = client.post(
                "/webhook",
                json=self._df_payload("แผลบวมแดงปวดมาก"),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json().get("fulfillmentText", "")
        self.assertIn("เฝ้าระวัง", body)
        self.assertNotIn("Recommend nurse", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
