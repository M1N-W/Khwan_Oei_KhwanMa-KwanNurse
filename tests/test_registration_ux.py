# -*- coding: utf-8 -*-
"""
Tests for KWN-06: Registration Quick Reply and Flex UX.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Setup environment variables
os.environ.setdefault("NURSE_GROUP_ID", "test_nurse_group")

from services.patient_profile import build_registration_quick_replies, build_profile_flex_summary
from routes.webhook import _make_dialogflow_response
from app import create_app


class TestRegistrationUXHelpers(unittest.TestCase):
    """Test pure helper functions for Registration UX builders."""

    def test_quick_replies_empty(self):
        self.assertEqual(build_registration_quick_replies([]), [])

    def test_quick_replies_consent(self):
        items = build_registration_quick_replies(["consent"])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["action"]["label"], "ยินยอม ✅")
        self.assertEqual(items[0]["action"]["text"], "ยินยอม")
        self.assertEqual(items[1]["action"]["label"], "ไม่ยินยอม ❌")
        self.assertEqual(items[1]["action"]["text"], "ไม่ยินยอม")

    def test_quick_replies_surgery_type(self):
        items = build_registration_quick_replies(["surgery_type"])
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["action"]["label"], "ผ่าตัดข้อเข่า")
        self.assertEqual(items[1]["action"]["label"], "ผ่าตัดข้อสะโพก")
        self.assertEqual(items[2]["action"]["label"], "ผ่าตัดอื่นๆ")

    def test_quick_replies_other_fields(self):
        self.assertEqual(build_registration_quick_replies(["first_name"]), [])
        self.assertEqual(build_registration_quick_replies(["last_name"]), [])
        self.assertEqual(build_registration_quick_replies(["hn"]), [])
        self.assertEqual(build_registration_quick_replies(["phone"]), [])

    def test_flex_summary_privacy(self):
        profile = {
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "hn": "HN12345",
            "phone": "0812345678",
            "registration_status": "registered",
            "consent_given": True,
            "line_user_id": "U1234567890abcdef"
        }
        msg = build_profile_flex_summary(profile)
        self.assertEqual(msg["type"], "flex")
        self.assertEqual(msg["altText"], "สรุปข้อมูลการลงทะเบียนของคุณ")
        
        contents = msg["contents"]
        self.assertEqual(contents["type"], "bubble")
        
        # Verify body contents recursively to handle nested premium layout
        def find_texts(item):
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    yield item["text"]
                for val in item.values():
                    yield from find_texts(val)
            elif isinstance(item, list):
                for sub in item:
                    yield from find_texts(sub)

        body_text_list = list(find_texts(contents["body"]))
        
        # HN is shown as-is
        self.assertTrue(any("HN12345" in text for text in body_text_list))
        # Phone is masked
        self.assertTrue(any("08X-XXX-5678" in text or "08x-xxx-5678" in text.lower() for text in body_text_list))
        # LINE user ID is NOT in the text
        self.assertFalse(any("U12345" in text for text in body_text_list))
        # Name is shown
        self.assertTrue(any("สมชาย ใจดี" in text for text in body_text_list))

        footer = contents["footer"]["contents"]
        buttons = [item for item in footer if item.get("type") == "button"]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["action"]["text"], "แก้ไขข้อมูล")


class TestWebhookIntegrationUX(unittest.TestCase):
    """Test webhook integrations with Flask Test Client."""

    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    @patch("config.ENABLE_RICH_MESSAGES", False)
    def test_make_dialogflow_response_disabled(self):
        res = _make_dialogflow_response("hello", [{"type": "action"}])
        self.assertEqual(res, {"fulfillmentText": "hello"})

    @patch("config.ENABLE_RICH_MESSAGES", True)
    def test_make_dialogflow_response_enabled_quick_replies(self):
        qrs = [{"type": "action", "action": {"type": "message", "label": "Yes", "text": "yes"}}]
        res = _make_dialogflow_response("hello", quick_replies=qrs)
        self.assertEqual(res["fulfillmentText"], "hello")
        self.assertIn("fulfillmentMessages", res)
        self.assertEqual(res["fulfillmentMessages"][0]["platform"], "LINE")
        self.assertEqual(res["fulfillmentMessages"][0]["payload"]["line"]["quickReply"]["items"], qrs)

    @patch("config.ENABLE_RICH_MESSAGES", True)
    def test_make_dialogflow_response_enabled_flex(self):
        flex = {"type": "flex", "altText": "alt", "contents": {}}
        res = _make_dialogflow_response("hello", flex_message=flex)
        self.assertEqual(res["fulfillmentText"], "hello")
        self.assertIn("fulfillmentMessages", res)
        self.assertEqual(res["fulfillmentMessages"][0]["platform"], "LINE")
        self.assertEqual(res["fulfillmentMessages"][0]["payload"]["line"], flex)

    @patch("config.ENABLE_RICH_MESSAGES", True)
    def test_active_flow_gets_cancel_button_and_one_time_hint(self):
        active = [{"name": "projects/p/agent/sessions/U1/contexts/registering", "lifespanCount": 5}]
        with self.app.test_request_context("/webhook", json={"queryResult": {"outputContexts": []}}):
            first = _make_dialogflow_response("กรุณาระบุชื่อ", output_contexts=active)
        items = first["fulfillmentMessages"][0]["payload"]["line"]["quickReply"]["items"]
        self.assertEqual(items[-1]["action"]["text"], "ยกเลิก")
        self.assertIn("พิมพ์ “ยกเลิก” ได้ทุกเมื่อ", first["fulfillmentText"])

        with self.app.test_request_context("/webhook", json={"queryResult": {"outputContexts": active}}):
            next_turn = _make_dialogflow_response("กรุณาระบุ HN", output_contexts=active)
        self.assertNotIn("พิมพ์ “ยกเลิก” ได้ทุกเมื่อ", next_turn["fulfillmentText"])


if __name__ == "__main__":
    unittest.main()
