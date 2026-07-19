"""Regression tests for direct LINE webhook bridging."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
os.environ.setdefault("FLASK_SECRET_KEY", "test-line-bridge")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DirectLineBridgeTests(unittest.TestCase):
    def test_text_event_calls_dialogflow_and_replies(self):
        from app import create_app

        app = create_app()
        detect_result = {
            "queryResult": {
                "intent": {"displayName": "ContactNurse"},
                "fulfillmentText": "เลือกหัวข้อได้เลยค่ะ",
                "fulfillmentMessages": [],
            }
        }
        with patch(
            "services.dialogflow_bridge.detect_intent", return_value=detect_result
        ) as detect, patch(
            "services.notification.reply_line_message"
        ) as reply:
            response = app.test_client().post(
                "/line/webhook",
                json={
                    "events": [{
                        "type": "message",
                        "replyToken": "reply-1",
                        "source": {"userId": "U1"},
                        "message": {"type": "text", "text": "ปรึกษาพยาบาล"},
                    }]
                },
            )

        self.assertEqual(response.status_code, 200)
        detect.assert_called_once_with("U1", "ปรึกษาพยาบาล")
        reply.assert_called_once_with("reply-1", "เลือกหัวข้อได้เลยค่ะ")

    def test_bridge_sends_registration_flex_for_complete_profile(self):
        from app import create_app

        app = create_app()
        detect_result = {
            "queryResult": {
                "intent": {"displayName": "PatientIdentity"},
                "fulfillmentText": "ข้อมูลลงทะเบียนครบแล้วค่ะ",
                "fulfillmentMessages": [],
            }
        }
        profile = {
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456",
            "citizen_id": "1709800000005",
            "phone": "0812345678",
            "consent_granted": True,
            "consent_version": "v1",
            "consent_at": "2026-07-19 16:00:00",
        }
        with patch("services.dialogflow_bridge.detect_intent", return_value=detect_result), \
             patch("database.patient_profile.read_patient_profile_result") as read, \
             patch("services.notification.reply_line_message_objects") as reply_objects:
            from database.patient_profile import PatientProfileReadResult
            read.return_value = PatientProfileReadResult(available=True, profile=profile)
            response = app.test_client().post(
                "/line/webhook",
                json={"events": [{
                    "type": "message",
                    "replyToken": "reply-2",
                    "source": {"userId": "U1"},
                    "message": {"type": "text", "text": "ข้อมูล"},
                }]},
            )

        self.assertEqual(response.status_code, 200)
        reply_objects.assert_called_once()
        self.assertEqual(reply_objects.call_args.args[0], "reply-2")
        self.assertEqual(reply_objects.call_args.args[1][0]["type"], "flex")


if __name__ == "__main__":
    unittest.main()
