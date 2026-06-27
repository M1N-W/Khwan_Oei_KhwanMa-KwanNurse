# -*- coding: utf-8 -*-
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app import app
from routes.webhook.handlers.symptoms import handle_report_symptoms
from routes.webhook.handlers.fallback import handle_contact_nurse, handle_after_hours_choice
from services.notification import _get_patient_prefix_label, build_symptom_notification, build_risk_notification, build_wound_alert_message, build_appointment_notification
from services.patient_profile import build_profile_flex_summary, enrich_registration_params
from services.line_message import flex_bubble, flex_text


class TestUIUXImprovements(unittest.TestCase):
    def test_pain_scale_mapping_and_validation(self):
        # 1. Test mapping 1 to 0
        with patch("routes.webhook.handlers.symptoms.calculate_symptom_risk") as mock_calc:
            mock_calc.return_value = "Mocked Patient Message"
            with app.app_context():
                response = handle_report_symptoms("U_TEST", {
                    "pain_score": "1",
                    "wound_status": "แผลแห้งดี",
                    "fever_check": "ไม่มีไข้",
                    "mobility_status": "เดินได้ปกติ"
                })
            # Check that calculate_symptom_risk was called with pain mapped to 0
            mock_calc.assert_called_once_with("U_TEST", 0, "แผลแห้งดี", "ไม่มีไข้", "เดินได้ปกติ", neuro=None)

        # 2. Test mapping 5 to 9
        with patch("routes.webhook.handlers.symptoms.calculate_symptom_risk") as mock_calc:
            mock_calc.return_value = "Mocked Patient Message"
            with app.app_context():
                response = handle_report_symptoms("U_TEST", {
                    "pain_score": "5",
                    "wound_status": "แผลแห้งดี",
                    "fever_check": "ไม่มีไข้",
                    "mobility_status": "เดินได้ปกติ"
                })
            # Check that calculate_symptom_risk was called with pain mapped to 9
            mock_calc.assert_called_once_with("U_TEST", 9, "แผลแห้งดี", "ไม่มีไข้", "เดินได้ปกติ", neuro=None)

    @patch("config.ENABLE_RICH_MESSAGES", True)
    @patch("routes.webhook.handlers.fallback.is_office_hours")
    def test_contact_nurse_quick_replies_office_hours(self, mock_office_hours):
        # Mock office hours = True
        mock_office_hours.return_value = True
        
        with app.app_context():
            response = handle_contact_nurse("U_TEST", {}, "")
        import json
        data = json.loads(response[0].data)
        
        # Verify category quick replies are included
        line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
        self.assertIn("quickReply", line_payload)
        items = line_payload["quickReply"]["items"]
        self.assertEqual(len(items), 5)
        self.assertEqual(items[0]["action"]["label"], "🚨 ฉุกเฉิน")
        self.assertEqual(items[0]["action"]["text"], "1")
        self.assertEqual(items[1]["action"]["label"], "💊 ถามเรื่องยา")
        self.assertEqual(items[1]["action"]["text"], "2")

    @patch("config.ENABLE_RICH_MESSAGES", True)
    @patch("routes.webhook.handlers.fallback.is_office_hours")
    def test_contact_nurse_quick_replies_after_hours(self, mock_office_hours):
        # Mock office hours = False
        mock_office_hours.return_value = False
        
        with app.app_context():
            response = handle_contact_nurse("U_TEST", {}, "")
        import json
        data = json.loads(response[0].data)
        
        # Verify after-hours quick replies are included
        line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
        self.assertIn("quickReply", line_payload)
        items = line_payload["quickReply"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["action"]["label"], "⏳ รอเวลาทำการ")
        self.assertEqual(items[1]["action"]["label"], "🚨 แจ้งเรื่องฉุกเฉิน")

    @patch("database.patient_profile.read_patient_profile")
    def test_get_patient_prefix_label(self, mock_read):
        # 1. Registered patient
        mock_read.return_value = {
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456"
        }
        label = _get_patient_prefix_label("U_REG")
        self.assertEqual(label, "มาวิน อยู่เย็น (HN: 123456)")

        # 2. Non-registered patient (fallback to user_id)
        mock_read.return_value = None
        label = _get_patient_prefix_label("U_NON_REG")
        self.assertEqual(label, "U_NON_REG")

    def test_profile_flex_summary_status_and_consent(self):
        # 1. Registered and consented
        from config import PATIENT_CONSENT_VERSION
        profile = {
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456",
            "phone": "0946477416",
            "registration_status": "registered",
            "consent_version": PATIENT_CONSENT_VERSION,
            "consent_at": "2026-06-27 12:00:00"
        }
        flex = build_profile_flex_summary(profile)
        body_box = flex["contents"]["body"]
        self.assertEqual(body_box["contents"][0]["text"], "✅ สถานะ: ลงทะเบียนแล้ว")
        self.assertEqual(body_box["contents"][5]["text"], "📋 ความยินยอม: ยินยอมแล้ว ✅")

        # 2. Incomplete and not consented
        profile_incomplete = {
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456",
            "phone": "",
            "registration_status": "incomplete",
            "consent_version": "",
            "consent_at": ""
        }
        flex_incomplete = build_profile_flex_summary(profile_incomplete)
        body_box_inc = flex_incomplete["contents"]["body"]
        self.assertEqual(body_box_inc["contents"][0]["text"], "⏳ สถานะ: ยังลงทะเบียนไม่ครบ")
        self.assertEqual(body_box_inc["contents"][5]["text"], "📋 ความยินยอม: ยังไม่ระบุ")

    def test_flex_bubble_spacing(self):
        bubble = flex_bubble(
            body_components=[flex_text("Body")],
            footer_components=[flex_text("Footer")]
        )
        self.assertEqual(bubble["body"]["spacing"], "md")
        self.assertEqual(bubble["footer"]["spacing"], "sm")

    def test_normalize_identity_fields_splits_full_name_always(self):
        from services.patient_profile import normalize_identity_fields
        # Test full name split even when last name is present in input
        res = normalize_identity_fields({
            "first_name": "นายมาวิน อยู่เย็น",
            "last_name": "อยู่เย็น"
        })
        self.assertEqual(res.get("first_name"), "นายมาวิน")
        self.assertEqual(res.get("last_name"), "อยู่เย็น")

    def test_prepare_registration_update_sets_registered_status(self):
        from services.patient_profile import prepare_registration_update
        from config import PATIENT_CONSENT_VERSION
        existing = {
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456"
        }
        params = {
            "phone": "0946477416",
            "consent": "yes"
        }
        update = prepare_registration_update(existing, params)
        self.assertEqual(update.profile.get("registration_status"), "registered")
