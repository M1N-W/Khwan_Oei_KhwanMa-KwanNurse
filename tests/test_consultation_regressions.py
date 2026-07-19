"""Regression coverage for consultation routing, cancellation, and UX fixes."""
from __future__ import annotations

import json
import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
os.environ.setdefault("FLASK_SECRET_KEY", "test-consultation-regressions")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ConsultationRoutingTests(unittest.TestCase):
    def test_patient_replies_include_cancel_and_retry_guidance(self):
        from app import create_app
        from routes.webhook.helpers import _append_patient_cancel_guidance

        app = create_app()
        response = app.response_class(
            '{"fulfillmentText":"✅ บันทึกคำขอเรียบร้อยแล้วค่ะ"}',
            mimetype="application/json",
        )
        updated = _append_patient_cancel_guidance((response, 200), "RequestAppointment")

        self.assertIn("พิมพ์ ‘ยกเลิก’", updated[0].get_json()["fulfillmentText"])

    def test_wound_photo_command_is_not_routed_to_knowledge(self):
        from app import create_app

        app = create_app()
        client = app.test_client()
        payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "ส่งรูปแผล",
                "intent": {"displayName": "GetKnowledge"},
                "parameters": {},
            },
        }
        response = client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("กรุณาส่งรูปแผล", response.get_json()["fulfillmentText"])

    def test_direct_line_text_registration_is_not_dropped(self):
        from app import create_app

        app = create_app()
        client = app.test_client()
        with patch("services.dialogflow_bridge.detect_intent") as detect, \
             patch("services.notification.reply_line_message") as reply:
            detect.return_value = {
                "queryResult": {
                    "intent": {"displayName": "PatientIdentity"},
                    "fulfillmentText": "กรุณาระบุชื่อ",
                    "fulfillmentMessages": [],
                }
            }
            response = client.post("/line/webhook", json={"events": [{
                "type": "message",
                "replyToken": "reply-1",
                "source": {"userId": "U1"},
                "message": {"type": "text", "text": "ลงทะเบียน"},
            }]})

        self.assertEqual(response.status_code, 200)
        detect.assert_called_once_with("U1", "ลงทะเบียน")
        reply.assert_called_once_with("reply-1", "กรุณาระบุชื่อ")
    def test_all_five_category_numbers_are_parseable(self):
        from services.teleconsult import parse_category_choice

        self.assertEqual(
            [parse_category_choice(str(number)) for number in range(1, 6)],
            ["emergency", "medication", "wound", "appointment", "other"],
        )
        self.assertEqual(parse_category_choice("ติดต่อพยาบาล"), "other")

    def test_contact_nurse_in_hours_exposes_category_context(self):
        from app import create_app
        from routes.webhook.handlers.fallback import handle_contact_nurse

        app = create_app()
        with patch("config.ENABLE_RICH_MESSAGES", True), app.test_request_context(
            "/webhook",
            json={"session": "projects/p/agent/sessions/U1"},
        ), patch("routes.webhook.handlers.fallback.is_office_hours", return_value=True):
            response, status = handle_contact_nurse("U1", {}, "ปรึกษาพยาบาล")

        payload = response.get_json()
        self.assertEqual(status, 200)
        self.assertEqual(payload["outputContexts"][0]["lifespanCount"], 5)
        self.assertIn("5. 👩🏻‍⚕️ ติดต่อพยาบาล", payload["fulfillmentText"])
        quick_reply_texts = [
            item["action"]["text"]
            for item in payload["fulfillmentMessages"][0]["payload"]["line"]["quickReply"]["items"]
        ]
        self.assertEqual(quick_reply_texts, ["1", "2", "3", "4", "5"])
        self.assertIn("ติดต่อพยาบาล", payload["fulfillmentMessages"][0]["payload"]["line"]["text"])

    def test_category_five_returns_direct_nurse_contact(self):
        from services.teleconsult import start_teleconsult

        result = start_teleconsult("U1", "other")

        self.assertTrue(result["success"])
        self.assertTrue(result["direct_contact"])
        self.assertIn("LINE ID: 0899181839", result["message"])
        self.assertIn("line.me/ti/p/~0899181839", result["message"])

    def test_category_five_rich_response_uses_button_without_url_preview(self):
        from app import create_app
        from routes.webhook.handlers.fallback import handle_contact_nurse

        app = create_app()
        with patch("config.ENABLE_RICH_MESSAGES", True), app.test_request_context(
            "/webhook", json={"session": "projects/p/agent/sessions/U1"}
        ), patch("routes.webhook.handlers.fallback.is_office_hours", return_value=True):
            response, status = handle_contact_nurse("U1", {"issue_category": "5"}, "5")

        payload = response.get_json()
        self.assertEqual(status, 200)
        line_payload = payload["fulfillmentMessages"][0]["payload"]["line"]
        self.assertEqual(line_payload["type"], "flex")
        footer_action = line_payload["contents"]["footer"]["contents"][0]["action"]
        self.assertEqual(footer_action["type"], "uri")
        self.assertNotIn("https://line.me", line_payload.get("altText", ""))


class ConsultationCancellationTests(unittest.TestCase):
    def test_after_hours_pending_cancellation_does_not_require_queue_row(self):
        from services import teleconsult

        with patch.object(
            teleconsult,
            "get_user_active_session",
            return_value={"Session_ID": "S1", "Status": "after_hours_pending"},
        ), patch.object(teleconsult, "update_session_status", return_value=True) as update, \
             patch.object(teleconsult, "remove_from_queue") as remove:
            result = teleconsult.cancel_consultation("U1")

        self.assertTrue(result["success"])
        update.assert_called_once()
        remove.assert_not_called()

    def test_queued_cancellation_fails_if_queue_row_cannot_be_removed(self):
        from services import teleconsult

        with patch.object(
            teleconsult,
            "get_user_active_session",
            return_value={"Session_ID": "S1", "Status": "queued"},
        ), patch.object(teleconsult, "update_session_status", return_value=True), \
             patch.object(teleconsult, "remove_from_queue", return_value=False):
            result = teleconsult.cancel_consultation("U1")

        self.assertFalse(result["success"])


class AppointmentStateTests(unittest.TestCase):
    def test_active_teleconsult_record_cannot_hijack_symptom_digit(self):
        from app import create_app

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "3",
                "intent": {"displayName": "Default Fallback Intent"},
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/reportsymptoms_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {},
                }],
            },
        }
        with patch(
            "routes.webhook.handler._has_active_teleconsult_session", return_value=True,
        ), patch(
            "routes.webhook.handler._dispatch_intent",
            return_value=({"fulfillmentText": "สภาพแผล"}, 200),
        ) as dispatch:
            response = app.test_client().post("/webhook", json=request_payload)

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with("ReportSymptoms", "U1", {"pain_score": "3"}, "3")

    def test_month_answer_preserves_day_and_ignores_inferred_date(self):
        from app import create_app
        from routes.webhook.handlers.symptoms import handle_request_appointment

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "กันยายน",
                "parameters": {"date": "2026-09-01"},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {"apt_day": "26"},
                }],
            },
        }
        with app.test_request_context("/webhook", json=request_payload):
            response, status = handle_request_appointment("U1", request_payload["queryResult"]["parameters"])

        payload = response.get_json()
        context_params = payload["outputContexts"][0]["parameters"]
        self.assertEqual(status, 200)
        self.assertEqual(context_params["apt_day"], "26")
        self.assertEqual(context_params["apt_month"], "9")
        self.assertIn("ปี พ.ศ.", payload["fulfillmentText"])

    def test_time_turn_is_not_saved_as_appointment_reason(self):
        from app import create_app
        from routes.webhook.handlers.symptoms import handle_request_appointment

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "14:30",
                "parameters": {
                    "time": "14:30",
                    "reason": "14:30",
                },
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {
                        "apt_day": "26",
                        "apt_month": "11",
                        "apt_year": "2026",
                        "waiting_for_custom_time": "true",
                    },
                }],
            },
        }
        with app.test_request_context("/webhook", json=request_payload):
            response, status = handle_request_appointment(
                "U1", request_payload["queryResult"]["parameters"]
            )

        payload = response.get_json()
        self.assertEqual(status, 200)
        self.assertIn("เหตุผลการนัดหมาย", payload["fulfillmentText"])
        context_params = payload["outputContexts"][0]["parameters"]
        self.assertEqual(context_params["preferred_time"], "14:30")
        self.assertNotIn("reason", context_params)

    def test_bare_morning_choice_sets_morning_time(self):
        from app import create_app
        from routes.webhook.handlers.symptoms import handle_request_appointment

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "เช้า",
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {
                        "apt_day": "26",
                        "apt_month": "11",
                        "apt_year": "2026",
                    },
                }],
            },
        }
        with app.test_request_context("/webhook", json=request_payload):
            response, status = handle_request_appointment("U1", {})

        payload = response.get_json()
        self.assertEqual(status, 200)
        self.assertIn("เหตุผลการนัดหมาย", payload["fulfillmentText"])
        self.assertEqual(payload["outputContexts"][0]["parameters"]["preferred_time"], "09:00")

    def test_past_date_response_clears_date_slots_for_restart(self):
        from app import create_app
        from routes.webhook.handlers.symptoms import handle_request_appointment

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "เหตุผลเดิม",
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {
                        "apt_day": "1",
                        "apt_month": "1",
                        "apt_year": "2026",
                        "preferred_time": "09:00",
                        "reason": "เหตุผลเดิม",
                    },
                }],
            },
        }
        with app.test_request_context("/webhook", json=request_payload):
            response, status = handle_request_appointment("U1", {})

        payload = response.get_json()
        context_params = payload["outputContexts"][0]["parameters"]
        self.assertEqual(status, 200)
        self.assertIn("วันที่ที่เลือกเป็นอดีต", payload["fulfillmentText"])
        self.assertNotIn("apt_day", context_params)
        self.assertNotIn("apt_month", context_params)
        self.assertNotIn("apt_year", context_params)

    def test_consultation_digit_wins_over_stale_appointment_context(self):
        from app import create_app

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "2",
                "intent": {"displayName": "Default Fallback Intent"},
                "parameters": {},
                "outputContexts": [
                    {
                        "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                        "lifespanCount": 5,
                        "parameters": {"apt_day": "26"},
                    },
                    {
                        "name": "projects/p/agent/sessions/U1/contexts/teleconsult_category_context",
                        "lifespanCount": 5,
                        "parameters": {},
                    },
                ],
            },
        }
        with patch(
            "routes.webhook.handler._dispatch_intent",
            return_value=({"fulfillmentText": "ยา"}, 200),
        ) as dispatch:
            response = app.test_client().post("/webhook", json=request_payload)

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with("AfterHoursChoice", "U1", {}, "2")

    def test_top_level_symptom_command_escapes_appointment_context(self):
        from app import create_app

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "รายงานอาการ",
                "intent": {"displayName": "ReportSymptoms"},
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {"apt_day": "1"},
                }],
            },
        }
        with patch(
            "routes.webhook.handler._dispatch_intent",
            return_value=({"fulfillmentText": "ระดับความปวด"}, 200),
        ) as dispatch:
            response = app.test_client().post("/webhook", json=request_payload)

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with("ReportSymptoms", "U1", {}, "รายงานอาการ")
        self.assertEqual(response.get_json()["outputContexts"][-1]["lifespanCount"], 0)

    def test_numeric_risk_answer_wins_over_misclassified_teleconsult_intent(self):
        from app import create_app

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "20",
                "intent": {"displayName": "AfterHoursChoice"},
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/assessrisk_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {"age": ""},
                }],
            },
        }
        with patch(
            "routes.webhook.handler._dispatch_intent",
            return_value=({"fulfillmentText": "น้ำหนัก"}, 200),
        ) as dispatch:
            response = app.test_client().post("/webhook", json=request_payload)

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with("AssessRisk", "U1", {"age": "20"}, "20")

    def test_appointment_date_wins_over_misclassified_after_hours_intent(self):
        from app import create_app

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "15",
                "intent": {"displayName": "AfterHoursChoice"},
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/requestappointment_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {"apt_month": "9"},
                }],
            },
        }
        with patch(
            "routes.webhook.handler._dispatch_intent",
            return_value=({"fulfillmentText": "เวลา"}, 200),
        ) as dispatch:
            response = app.test_client().post("/webhook", json=request_payload)

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with(
            "RequestAppointment", "U1", {"apt_month": "9"}, "15"
        )

    def test_risk_negative_answer_is_mapped_to_disease_slot_when_intent_is_wrong(self):
        from app import create_app

        app = create_app()
        request_payload = {
            "session": "projects/p/agent/sessions/U1",
            "queryResult": {
                "queryText": "ไม่มีโรคประจำตัว",
                "intent": {"displayName": "AfterHoursChoice"},
                "parameters": {},
                "outputContexts": [{
                    "name": "projects/p/agent/sessions/U1/contexts/assessrisk_dialog_context",
                    "lifespanCount": 5,
                    "parameters": {
                        "age": "16",
                        "weight": "167",
                        "height": "178",
                        "disease": "",
                    },
                }],
            },
        }
        with patch(
            "routes.webhook.handler._dispatch_intent",
            return_value=({"fulfillmentText": "บันทึกแล้ว"}, 200),
        ) as dispatch:
            response = app.test_client().post("/webhook", json=request_payload)

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with(
            "AssessRisk",
            "U1",
            {
                "age": "16",
                "weight": "167",
                "height": "178",
                "disease": "ไม่มีโรคประจำตัว",
            },
            "ไม่มีโรคประจำตัว",
        )


class AlertFormattingTests(unittest.TestCase):
    def test_symptom_alert_is_compact_and_does_not_leak_debug_fields(self):
        from services import notification

        with patch.object(notification, "_get_patient_prefix_label", return_value="นายทดสอบ (HN: 123)"):
            message = notification.build_symptom_notification(
                "U-secret", 0, "บวมแดง", "มีไข้", "เดินได้", "เสี่ยงสูง", 4
            )

        self.assertIn("นายทดสอบ (HN: 123)", message)
        self.assertIn("⚡ กรุณาตรวจสอบทันที", message)
        self.assertNotIn("User ID", message)
        self.assertNotIn("Flags", message)
        self.assertNotIn("───────────────", message)


class SheetsConfigurationTests(unittest.TestCase):
    def test_base64_google_credentials_are_decoded_for_gspread(self):
        from database import sheets

        credentials = {"type": "service_account", "project_id": "test-project"}
        encoded = base64.b64encode(json.dumps(credentials).encode()).decode()
        cache = sheets._get_local_cache()
        sheets.invalidate_sheet_client()
        with patch.object(sheets, "GSPREAD_CREDENTIALS", ""), \
             patch.dict(os.environ, {"GOOGLE_CREDS_B64": encoded}), \
             patch.object(sheets.gspread, "service_account_from_dict", return_value="client") as factory:
            self.assertEqual(sheets.get_sheet_client(), "client")
        factory.assert_called_once_with(credentials)
        sheets.invalidate_sheet_client()


if __name__ == "__main__":
    unittest.main()
