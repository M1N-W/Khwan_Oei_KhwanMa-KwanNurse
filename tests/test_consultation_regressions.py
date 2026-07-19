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
        with patch("routes.webhook.handler._dispatch_intent") as dispatch:
            dispatch.return_value = (app.response_class(
                '{"fulfillmentText":"กรุณาระบุชื่อ"}', mimetype="application/json"
            ), 200)
            response = client.post("/line/webhook", json={"events": [{
                "type": "message",
                "replyToken": "reply-1",
                "source": {"userId": "U1"},
                "message": {"type": "text", "text": "ลงทะเบียน"},
            }]})

        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with("PatientIdentity", "U1", {}, "ลงทะเบียน")
    def test_all_five_category_numbers_are_parseable(self):
        from services.teleconsult import parse_category_choice

        self.assertEqual(
            [parse_category_choice(str(number)) for number in range(1, 6)],
            ["emergency", "medication", "wound", "appointment", "other"],
        )

    def test_contact_nurse_in_hours_exposes_category_context(self):
        from app import create_app
        from routes.webhook.handlers.fallback import handle_contact_nurse

        app = create_app()
        with app.test_request_context(
            "/webhook",
            json={"session": "projects/p/agent/sessions/U1"},
        ), patch("routes.webhook.handlers.fallback.is_office_hours", return_value=True):
            response, status = handle_contact_nurse("U1", {}, "ปรึกษาพยาบาล")

        payload = response.get_json()
        self.assertEqual(status, 200)
        self.assertEqual(payload["outputContexts"][0]["lifespanCount"], 5)
        self.assertIn("5. ❓ อื่นๆ", payload["fulfillmentText"])


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
