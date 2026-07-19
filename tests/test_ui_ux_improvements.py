# -*- coding: utf-8 -*-
import os
import sys
import json
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
    def test_report_symptoms_prompt_keeps_runtime_context(self):
        from routes.webhook.handlers.symptoms import handle_report_symptoms

        payload = {
            "session": "projects/test/agent/sessions/line-session",
            "queryResult": {"parameters": {"pain_score": ""}},
        }
        with app.test_request_context("/webhook", method="POST", json=payload):
            response, status = handle_report_symptoms("U_TEST", {"pain_score": ""})

        data = response.get_json()
        self.assertEqual(status, 200)
        contexts = data["outputContexts"]
        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0]["name"].endswith("/contexts/reportsymptoms_dialog_context"))
        self.assertEqual(contexts[0]["lifespanCount"], 5)

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

        # 3. Test mobility "ต้องพยุง" maps to moderate risk (+1)
        from services.clinical_engine import evaluate_symptom_risk, SymptomClinicalInput
        inputs = SymptomClinicalInput(
            pain=0,
            wound="แผลแห้งดี",
            fever="ไม่มีไข้",
            mobility="ต้องพยุง",
            neuro=None
        )
        res = evaluate_symptom_risk(inputs)
        self.assertEqual(res.risk_score, 1) # Moderate risk
        self.assertIn("เคลื่อนไหวลำบาก", "".join(res.risk_details))

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

        # 2. Non-registered patient must not expose the LINE user ID
        mock_read.return_value = None
        label = _get_patient_prefix_label("U_NON_REG")
        self.assertEqual(label, "ไม่ระบุชื่อ (ยังไม่ลงทะเบียน)")

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
        self.assertEqual(body_box["contents"][0]["contents"][0]["contents"][0]["text"], "✅ ลงทะเบียนแล้ว")
        self.assertEqual(body_box["contents"][6]["contents"][1]["contents"][1]["text"], "ยินยอมแล้ว ✅")

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
        self.assertEqual(body_box_inc["contents"][0]["contents"][0]["contents"][0]["text"], "⏳ ยังลงทะเบียนไม่ครบ")
        self.assertEqual(body_box_inc["contents"][6]["contents"][1]["contents"][1]["text"], "ยังไม่ระบุ")

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
            "hn": "123456",
            "citizen_id": "1234567890121"
        }
        params = {
            "phone": "0946477416",
            "consent": "yes"
        }
        update = prepare_registration_update(existing, params)
        self.assertEqual(update.profile.get("registration_status"), "registered")

    def test_worksheet_monkeypatch_cache_and_invalidation(self):
        from database.sheets import _patch_worksheet_read_methods
        mock_sheet = MagicMock()
        mock_sheet.title = "TestSheet"
        mock_sheet.get_all_values.return_value = [["Header"], ["Row1"]]
        mock_sheet.append_row.return_value = {}

        # Bypass the is_testing check by patching sys.modules
        with patch("sys.modules", {"unittest": None}):
            _patch_worksheet_read_methods(mock_sheet)
            
            # First read should hit original get_all_values
            v1 = mock_sheet.get_all_values()
            self.assertEqual(v1, [["Header"], ["Row1"]])
            
            # Second read should be cached
            v2 = mock_sheet.get_all_values()
            self.assertEqual(v2, [["Header"], ["Row1"]])

            # Write operation (append_row) should invalidate the cache
            mock_sheet.append_row([["Row2"]])
            
            # Third read should fetch again from original
            v3 = mock_sheet.get_all_values()
            self.assertEqual(v3, [["Header"], ["Row1"]])

    @patch("database.patient_profile.read_patient_profile")
    def test_get_patient_prefix_label_trims_duplicate_name(self, mock_read):
        # Test trimming duplicate surname in first_name (e.g. "มาวิน อยู่เย็น อยู่เย็น" -> "มาวิน อยู่เย็น")
        mock_read.return_value = {
            "first_name": "มาวิน อยู่เย็น",
            "last_name": "อยู่เย็น",
            "hn": "123456"
        }
        label = _get_patient_prefix_label("U_REG")
        self.assertEqual(label, "มาวิน อยู่เย็น (HN: 123456)")

        mock_read.return_value = {
            "first_name": "มาวิน อยู่เย็น อยู่เย็น",
            "last_name": "อยู่เย็น",
            "hn": "123456"
        }
        label2 = _get_patient_prefix_label("U_REG2")
        self.assertEqual(label2, "มาวิน อยู่เย็น (HN: 123456)")

    @patch("database.patient_profile.read_patient_profile")
    @patch("services.presession.build_pre_consult_briefing_data")
    @patch("config.ENABLE_RICH_MESSAGES", True)
    def test_build_emergency_flex_and_text_alerts(self, mock_briefing, mock_read):
        mock_read.return_value = {
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456",
            "phone": "0946477416"
        }
        mock_briefing.return_value = {
            "risk": "high",
            "summary": "คนไข้มีอาการเหนื่อยหอบ",
            "questions": ["คำถามข้อที่ 1"]
        }

        # 1. Flex message
        from services.notification import build_emergency_flex_alert
        flex = build_emergency_flex_alert("U_REG", "หายใจไม่สะดวก")
        self.assertEqual(flex["type"], "flex")
        self.assertIn("ฉุกเฉิน", flex["altText"])
        bubble = flex["contents"]
        self.assertEqual(bubble["header"]["backgroundColor"], "#DC3545")
        
        # Verify no User ID and no Session ID in body contents
        import json
        body_text = json.dumps(bubble["body"])
        self.assertNotIn("User ID", body_text)
        self.assertNotIn("TC", body_text)
        
        # Verify phone number dialing action exists in footer
        footer_buttons = bubble["footer"]["contents"]
        self.assertEqual(footer_buttons[0]["action"]["uri"], "tel:0946477416")

        # 2. Text message fallback
        from services.notification import build_emergency_text_alert
        text = build_emergency_text_alert("U_REG", "หายใจไม่สะดวก")
        self.assertIn("ผู้ป่วย: มาวิน อยู่เย็น", text)
        self.assertIn("อาการ: หายใจไม่สะดวก", text)

    @patch("database.patient_profile.read_patient_profile_result")
    @patch("database.patient_profile.upsert_patient_profile")
    @patch("services.llm.complete")
    def test_ai_mode_activation_deactivation_and_consultation(self, mock_complete, mock_upsert, mock_read_result):
        from config import PATIENT_CONSENT_VERSION
        from database.patient_profile import PatientProfileReadResult
        # Setup mock profile
        profile = {
            "user_id": "U_TEST_AI",
            "first_name": "มาวิน",
            "last_name": "อยู่เย็น",
            "hn": "123456",
            "citizen_id": "1234567890121",
            "phone": "0946477416",
            "registration_status": "registered",
            "consent_version": PATIENT_CONSENT_VERSION,
            "consent_at": "2026-06-27 12:00:00",
            "ai_mode": False
        }
        mock_read_result.return_value = PatientProfileReadResult(available=True, profile=profile)
        mock_complete.return_value = "แนะนำตัวยาชนิดนี้ค่ะ"

        # 1. Try to activate AI mode
        with app.app_context():
            response = app.test_client().post("/webhook", json={
                "queryResult": {
                    "queryText": "คุยกับเอไอ",
                    "intent": {"displayName": "Default Fallback Intent"}
                },
                "session": "projects/dummy/agent/sessions/U_TEST_AI"
            })
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("ยินดีต้อนรับเข้าสู่โหมดคุยกับ AI", data["fulfillmentText"])
            mock_upsert.assert_called_with("U_TEST_AI", {"ai_mode": True})

        # 2. Consultation when AI mode is active
        profile["ai_mode"] = True
        with app.app_context():
            response = app.test_client().post("/webhook", json={
                "queryResult": {
                    "queryText": "ปวดแผลทำอย่างไรดีครับ",
                    "intent": {"displayName": "Default Fallback Intent"}
                },
                "session": "projects/dummy/agent/sessions/U_TEST_AI"
            })
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["fulfillmentText"], "แนะนำตัวยาชนิดนี้ค่ะ")
            mock_complete.assert_called()

        # 3. Deactivate AI mode
        with app.app_context():
            response = app.test_client().post("/webhook", json={
                "queryResult": {
                    "queryText": "คุยกับพยาบาล",
                    "intent": {"displayName": "Default Fallback Intent"}
                },
                "session": "projects/dummy/agent/sessions/U_TEST_AI"
            })
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("ออกจากโหมดคุยกับ AI เรียบร้อยแล้วค่ะ", data["fulfillmentText"])
            mock_upsert.assert_called_with("U_TEST_AI", {"ai_mode": False})

    @patch("config.ENABLE_RICH_MESSAGES", True)
    def test_progressive_slot_filling_and_escape_hatch(self):
        # 1. Test RequestAppointment progressive prompt for time
        with app.app_context():
            from routes.webhook.handlers.symptoms import handle_request_appointment
            response = handle_request_appointment("U_TEST", {
                "date": "2026-06-30",
                "time": "",
                "reason": ""
            })
            data = json.loads(response[0].data)
            self.assertIn("เวลาที่ต้องการนัดหมาย", data["fulfillmentText"])
            items = data["fulfillmentMessages"][0]["payload"]["line"]["quickReply"]["items"]
            self.assertEqual(len(items), 3)
            self.assertEqual(items[0]["action"]["text"], "เช้า")

        # 2. Test AssessRisk progressive prompt for diseases
        with app.app_context():
            from routes.webhook.handlers.symptoms import handle_assess_risk
            response = handle_assess_risk("U_TEST", {
                "age": 45,
                "weight": 70,
                "height": 175,
                "diseases": ""
            })
            data = json.loads(response[0].data)
            self.assertIn("โรคประจำตัว", data["fulfillmentText"])
            items = data["fulfillmentMessages"][0]["payload"]["line"]["quickReply"]["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["action"]["text"], "ไม่มี")

        # 3. A free-text negative disease answer completes the flow instead
        # of restarting at the age prompt.
        with app.app_context():
            with patch("routes.webhook.handlers.symptoms.calculate_personal_risk", return_value="risk result") as calc:
                response = handle_assess_risk("U_TEST", {
                    "age": 25,
                    "weight": 57,
                    "height": 170,
                    "disease": "ไม่มี",
                })
                data = json.loads(response[0].data)
                self.assertEqual(data["fulfillmentText"], "risk result")
                calc.assert_called_once_with("U_TEST", 25, 57, 170, "ไม่มีโรคประจำตัว")

        # 4. Test escape hatch "ยกเลิก"
        with app.app_context():
            response = app.test_client().post("/webhook", json={
                "queryResult": {
                    "queryText": "ยกเลิก",
                    "intent": {"displayName": "Default Fallback Intent"}
                },
                "session": "projects/dummy/agent/sessions/U_TEST_ESCAPE"
            })
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("ยกเลิกการทำรายการเรียบร้อยแล้วค่ะ", data["fulfillmentText"])
            contexts = data["outputContexts"]
            # Verify contexts are cleared
            self.assertEqual(contexts[0]["lifespanCount"], 0)
