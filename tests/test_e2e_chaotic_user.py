# -*- coding: utf-8 -*-
"""
End-to-end integration tests for chaotic user behaviors.
Tests the defensive mechanisms added for global exception safety, context checks, and robust fallbacks.
"""
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, Mock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
os.environ.setdefault("GSPREAD_CREDENTIALS", "")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class E2EChaoticUserTests(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        from app import create_app
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def _df_payload(self, intent_name, query_text="test", params=None, session_id="u-chaotic"):
        return {
            "responseId": "test-response-id",
            "session": f"projects/p/agent/sessions/{session_id}",
            "queryResult": {
                "queryText": query_text,
                "parameters": params or {},
                "intent": {"displayName": intent_name},
                "outputContexts": []
            }
        }

    def test_global_exception_handling_dialogflow(self):
        """Phase 2: Verify unhandled exception in webhook route returns fallback text."""
        payload = self._df_payload("GetKnowledge", "ความรู้")
        with patch("routes.webhook.handler.incr", side_effect=Exception("Flask global exception simulation")):
            resp = self.client.post("/webhook", json=payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("ขออภัยค่ะ ขณะนี้ระบบขัดข้องชั่วคราว", data.get("fulfillmentText", ""))

    def test_global_exception_handling_line_webhook(self):
        """Phase 2: Verify unhandled exception in LINE webhook route replies with fallback text and returns 200."""
        payload = {
            "events": [
                {
                    "type": "message",
                    "replyToken": "test_reply_token",
                    "source": {"userId": "U123"},
                    "message": {"type": "image", "id": "img123"}
                }
            ]
        }
        with patch("routes.webhook.handle_line_image_event", side_effect=Exception("Image handler crashed")), \
             patch("services.notification.reply_line_message") as reply_mock:
            resp = self.client.post("/line/webhook", json=payload)
            self.assertEqual(resp.status_code, 200)
            reply_mock.assert_called_once_with(
                "test_reply_token", 
                "⚠️ ขออภัยค่ะ ขณะนี้ระบบขัดข้องชั่วคราว ทีมงานกำลังเร่งแก้ไข กรุณาลองใหม่อีกครั้งในภายหลังค่ะ"
            )

    def test_chaotic_registration_emergency_interruption(self):
        """Phase 1 & 4: Chaotic user interrupts registration flow with emergency/cancel logic."""
        # Registering flow with cancel keyword
        payload = self._df_payload("PatientIdentity", "cancel")
        from database.patient_profile import PatientProfileReadResult
        incomplete_profile = {
            "first_name": "สมคิด", "last_name": "", "hn": "", "phone": "",
            "consent_granted": True, "registration_status": "incomplete"
        }
        with patch("database.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, incomplete_profile)), \
             patch("database.patient_profile.upsert_patient_profile") as upsert_mock, \
             patch("services.patient_profile.invalidate_profile_cache"):
            resp = self.client.post("/webhook", json=payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("ยกเลิกการลงทะเบียนเรียบร้อยแล้ว", data.get("fulfillmentText", ""))
            upsert_mock.assert_called_once()
            # Verify names and flags were reset
            cleared_profile = upsert_mock.call_args[0][1]
            self.assertEqual(cleared_profile["first_name"], "")

    def test_chaotic_teleconsult_invalid_choices(self):
        """Phase 4: Chaotic user replies with invalid choices for teleconsult after-hours."""
        from routes.webhook.handlers.fallback import handle_after_hours_choice
        
        # Office hours off to test after-hours flow
        with patch("services.teleconsult.is_office_hours", return_value=False):
            with self.app.app_context():
                res, code = handle_after_hours_choice("U-test", "99")
                self.assertEqual(code, 200)
                data = res.get_json()
                self.assertIn("หมายเลข 1 หรือ 2", data.get("fulfillmentText", ""))

    def test_chaotic_symptoms_invalid_pain_score(self):
        """Phase 4: Chaotic user provides out-of-bound pain scores or invalid inputs."""
        # Pain score is 99 (out of bound)
        payload = self._df_payload("ReportSymptoms", "เจ็บแผล", {
            "pain_score": "99",
            "wound_status": "แดงและบวม",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้"
        })
        
        # Webhook should handle it gracefully, capping/normalizing the pain score or flagging it appropriately
        from database.patient_profile import PatientProfileReadResult
        from config import PATIENT_CONSENT_VERSION
        registered_profile = {
            "first_name": "สมคิด", 
            "last_name": "ใจดี",
            "hn": "HN001",
            "citizen_id": "1234567890121",
            "phone": "0812345678",
            "consent_version": PATIENT_CONSENT_VERSION,
            "consent_at": "2026-06-28T12:00:00",
            "registration_status": "registered"
        }
        with patch("database.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, registered_profile)), \
             patch("services.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, registered_profile)), \
             patch("database.patient_profile.upsert_patient_profile", return_value=True), \
             patch("services.llm._call_gemini", return_value=json.dumps({"severity": "high", "observations": ["มีแผลแยก"], "advice": "แนะนำมา รพ."})), \
             patch("database.sheets.save_symptom_data", return_value=True):
            resp = self.client.post("/webhook", json=payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("ระดับความเสี่ยง", data.get("fulfillmentText", ""))

    def test_chaotic_appointments_invalid_date_time(self):
        """Phase 4: Chaotic user provides invalid date ("31 กุมภาพันธ์") or invalid time format ("25:00")."""
        from routes.webhook.handlers.symptoms import handle_request_appointment
        
        # Test case: February 31st
        params = {
            "apt_day": "31",
            "apt_month": "2",  # February
            "apt_time": "10:00",
            "apt_reason": "ตรวจแผล"
        }
        
        from database.patient_profile import PatientProfileReadResult
        from config import PATIENT_CONSENT_VERSION
        registered_profile = {
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "hn": "HN001",
            "citizen_id": "1234567890121",
            "phone": "0812345678",
            "consent_version": PATIENT_CONSENT_VERSION,
            "consent_at": "2026-06-28T12:00:00",
            "registration_status": "registered"
        }
        
        with patch("database.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, registered_profile)), \
             patch("services.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, registered_profile)), \
             patch("database.patient_profile.upsert_patient_profile", return_value=True):
            with self.app.app_context():
                res, code = handle_request_appointment("U-test", params)
                self.assertEqual(code, 200)
                data = res.get_json()
                # Reroutes back or prompts for date correction
                self.assertIn("กรุณาระบุ", data.get("fulfillmentText", ""))

if __name__ == "__main__":
    unittest.main()
