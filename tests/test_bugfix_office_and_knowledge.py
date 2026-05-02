# -*- coding: utf-8 -*-
"""
Regression tests for two production bugs reported on 2026-04-26:

**Bug 1** — *Office hours showed "after-hours" on Sunday at 15:35 with the*
*message displaying ``08:00-18:00 น.``* without explaining Mon-Fri only.
Resolved by widening ``OFFICE_HOURS`` to 7-day 06:00-22:00 and updating
the "(จันทร์-ศุกร์)" copy to "(ทุกวัน)".

**Bug 2** — *User typed "ดูแลแผล" right after the knowledge menu and got*
*the same menu back instead of the wound-care guide.* Root cause: the
Dialogflow ``GetKnowledge`` intent had no ``KnowledgeTopic`` entity so
``params['topic']`` was always empty. Resolved by:
1. Adding ``KnowledgeTopic`` entity + annotating training phrases
   (Dialogflow side, not testable here without a live agent).
2. Falling back to scanning ``query_text`` against the topic keyword map
   when the param is missing — *this* is what we test here.

Run::

    python -m unittest test_bugfix_office_and_knowledge.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# Bug 1: OFFICE_HOURS now covers every day, 06:00-22:00.
# -----------------------------------------------------------------------------
class OfficeHoursConfigTests(unittest.TestCase):

    def test_config_is_seven_day(self):
        from config import OFFICE_HOURS
        self.assertEqual(set(OFFICE_HOURS["weekdays"]), {0, 1, 2, 3, 4, 5, 6})
        self.assertEqual(OFFICE_HOURS["start"], "06:00")
        self.assertEqual(OFFICE_HOURS["end"], "22:00")

    def test_is_office_hours_true_on_sunday_afternoon(self):
        """The exact scenario from the production screenshot."""
        from services import teleconsult
        from config import LOCAL_TZ

        # 2026-04-26 15:35 Bangkok = Sunday 15:35
        fake_now = datetime(2026, 4, 26, 15, 35, tzinfo=LOCAL_TZ)

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now if tz is None else fake_now.astimezone(tz)

            @classmethod
            def strptime(cls, *args, **kwargs):
                return datetime.strptime(*args, **kwargs)

        with patch.object(teleconsult, "datetime", _DT):
            self.assertTrue(teleconsult.is_office_hours())

    def test_is_office_hours_false_outside_window(self):
        from services import teleconsult
        from config import LOCAL_TZ
        fake_now = datetime(2026, 4, 26, 5, 30, tzinfo=LOCAL_TZ)  # 05:30 — too early

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now if tz is None else fake_now.astimezone(tz)

            @classmethod
            def strptime(cls, *args, **kwargs):
                return datetime.strptime(*args, **kwargs)

        with patch.object(teleconsult, "datetime", _DT):
            self.assertFalse(teleconsult.is_office_hours())

    def test_after_hours_message_says_every_day(self):
        """Defensive copy check — never advertise Mon-Fri again."""
        from services import teleconsult
        # Read the file as text and assert the historical "(จันทร์-ศุกร์)"
        # is gone — replaced with a 7-day copy.
        src = Path(teleconsult.__file__).read_text(encoding="utf-8")
        self.assertNotIn("(จันทร์-ศุกร์)", src,
                         "Outdated office-hours copy still present")
        self.assertIn("(ทุกวัน)", src,
                      "7-day office-hours copy missing")


# -----------------------------------------------------------------------------
# Bug 2: handle_get_knowledge falls back to query_text when params miss topic.
# -----------------------------------------------------------------------------
class GetKnowledgeFallbackTests(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY",
                              "test-secret-for-bugfix-knowledge")
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _get_text(self, resp):
        return resp.get_json().get("fulfillmentText", "")

    def _post(self, query_text, params=None):
        return self.client.post("/webhook", json={
            "queryResult": {
                "intent": {"displayName": "GetKnowledge"},
                "parameters": params or {},
                "queryText": query_text,
            },
            "session": "projects/x/agent/sessions/U-bugfix-knowledge-test-12345",
        })

    # --- Production scenario: empty params + wound-care text ----------------
    def test_dulae_phlae_with_empty_params_returns_wound_guide_not_menu(self):
        """The exact production bug from the screenshot."""
        resp = self._post("ดูแลแผล", params={})
        self.assertEqual(resp.status_code, 200)
        text = self._get_text(resp)
        # ✅ Should be wound care guide (mentions wound terms), not menu.
        self.assertNotIn("เลือกหัวข้อที่ต้องการเรียนรู้", text,
                         "Bug regression: returned menu instead of guide")
        # The wound care guide always has these substrings
        self.assertTrue(
            any(token in text for token in ("ดูแลแผล", "ทำความสะอาดแผล", "แผล")),
            f"Expected wound-care content; got: {text[:200]}",
        )

    # --- Other topic words via query_text -----------------------------------
    def test_dvt_via_query_text(self):
        text = self._get_text(self._post("DVT", params={}))
        self.assertNotIn("เลือกหัวข้อที่ต้องการเรียนรู้", text)
        self.assertIn("ลิ่มเลือด", text)

    def test_thanyaa_via_query_text(self):
        text = self._get_text(self._post("ทานยา", params={}))
        self.assertNotIn("เลือกหัวข้อที่ต้องการเรียนรู้", text)
        self.assertIn("ยา", text)

    # --- Param still wins when present (backward compat) --------------------
    def test_param_topic_still_used_when_present(self):
        text = self._get_text(self._post(
            "อะไรก็ได้",
            params={"topic": "wound_care"},
        ))
        self.assertNotIn("เลือกหัวข้อที่ต้องการเรียนรู้", text)

    # --- Menu trigger words still go to menu --------------------------------
    def test_menu_request_still_returns_menu(self):
        text = self._get_text(self._post("ความรู้", params={}))
        self.assertIn("เลือกหัวข้อที่ต้องการเรียนรู้", text)

    # --- Substring match for natural-language requests ----------------------
    def test_substring_match_in_natural_phrase(self):
        text = self._get_text(self._post(
            "อยากรู้เรื่องดูแลแผลค่ะ", params={},
        ))
        self.assertNotIn("เลือกหัวข้อที่ต้องการเรียนรู้", text)
        # Should pick wound_care, not the menu
        self.assertTrue(
            "ดูแลแผล" in text or "ทำความสะอาดแผล" in text,
            f"Expected wound-care content; got: {text[:200]}",
        )

    # --- Longest-match wins for ambiguous substrings ------------------------
    def test_longer_phrase_beats_shorter_substring(self):
        from routes.webhook import _resolve_knowledge_topic
        # Both "ลิ่มเลือด" and "ป้องกันลิ่มเลือด" are keys; longer should win.
        # Both map to the same guide (dvt_prevention) so this just guarantees
        # the substring match logic doesn't crash on overlap.
        result = _resolve_knowledge_topic("ป้องกันลิ่มเลือด")
        self.assertIsNotNone(result)
        topic_name, _ = result
        self.assertEqual(topic_name, "ป้องกันลิ่มเลือด")


# -----------------------------------------------------------------------------
# Bug 2 (Dialogflow side): KnowledgeTopic entity exists and intent declares it.
# -----------------------------------------------------------------------------
class DialogflowAgentSchemaTests(unittest.TestCase):

    def test_knowledge_topic_entity_file_exists(self):
        path = Path("dialogflow/entities/KnowledgeTopic.json")
        self.assertTrue(path.exists(),
                        "KnowledgeTopic entity definition missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "KnowledgeTopic")

    def test_knowledge_topic_entity_has_all_topics(self):
        path = Path("dialogflow/entities/KnowledgeTopic_entries_th.json")
        self.assertTrue(path.exists())
        entries = json.loads(path.read_text(encoding="utf-8"))
        values = {entry["value"] for entry in entries}
        expected = {"wound_care", "physical_therapy", "dvt_prevention",
                    "medication", "warning_signs"}
        self.assertEqual(values, expected,
                         f"Topic values mismatch. Got: {values}")

    def test_get_knowledge_intent_declares_topic_param(self):
        path = Path("dialogflow/intents/GetKnowledge.json")
        intent = json.loads(path.read_text(encoding="utf-8"))
        params = intent["responses"][0]["parameters"]
        self.assertTrue(params, "GetKnowledge intent has no parameters")
        topic_param = next((p for p in params if p["name"] == "topic"), None)
        self.assertIsNotNone(topic_param, "topic parameter missing")
        self.assertEqual(topic_param["dataType"], "@KnowledgeTopic")

    def test_training_phrase_dulae_phlae_is_annotated(self):
        path = Path("dialogflow/intents/GetKnowledge_usersays_th.json")
        phrases = json.loads(path.read_text(encoding="utf-8"))
        target = next(
            (p for p in phrases
             if any(seg.get("text") == "ดูแลแผล" for seg in p["data"])),
            None,
        )
        self.assertIsNotNone(target, "'ดูแลแผล' training phrase missing")
        seg = next(s for s in target["data"] if s.get("text") == "ดูแลแผล")
        self.assertEqual(seg.get("meta"), "@KnowledgeTopic",
                         "'ดูแลแผล' is not annotated as KnowledgeTopic entity")
        self.assertEqual(seg.get("alias"), "topic")


if __name__ == "__main__":
    unittest.main()
