# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PatientRegistrationSheetContractTests(unittest.TestCase):

    def test_empty_worksheet_writes_full_header_and_one_row(self):
        from database import patient_profile as pp_db

        captured = []

        class _Sheet:
            def get_all_values(self):
                return []

            def append_row(self, row, value_input_option=None):
                captured.append(row)

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U1", {"first_name": "สมชาย"})

        self.assertTrue(ok)
        self.assertEqual(captured[0], pp_db.HEADERS)
        self.assertEqual(len(captured[0]), 16)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[1][0], "U1")

    def test_upsert_extends_legacy_header_and_preserves_unknown_columns(self):
        from database import patient_profile as pp_db

        captured = {"appended": []}

        class _Sheet:
            def get_all_values(self):
                return [
                    [
                        "User_ID", "Age", "Sex", "Surgery_Type", "Surgery_Date",
                        "Diseases", "Updated_At", "First_Name", "Last_Name", "HN",
                        "Future_Field",
                    ],
                    [
                        "U1", "61", "f", "knee", "", "เบาหวาน",
                        "2026-01-01 09:00:00", "สมหญิง", "ดี", "HN001",
                        "future-value",
                    ],
                ]

            def update(self, target_range, values, value_input_option=None):
                captured.setdefault("updates", []).append((target_range, values))

            def append_row(self, row, value_input_option=None):
                captured["appended"].append(row)

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U1", {
                "first_name": "สมหญิง",
                "last_name": "ดี",
                "hn": "HN001",
                "phone": "0812345678",
                "consent_granted": True,
            })

        self.assertTrue(ok)
        header_update = captured["updates"][0]
        row_update = captured["updates"][1]
        self.assertEqual(header_update[0], "A1:Q1")
        headers = header_update[1][0]
        self.assertEqual(headers[:10], pp_db.HEADERS[:10])
        self.assertEqual(headers[10], "Future_Field")
        for header in pp_db.HEADERS[10:]:
            self.assertIn(header, headers)
        self.assertEqual(row_update[0], "A2:Q2")
        row = row_update[1][0]
        self.assertEqual(row[headers.index("Phone")], "0812345678")
        self.assertEqual(row[headers.index("Registration_Status")], "registered")
        self.assertEqual(row[headers.index("Future_Field")], "future-value")

    def test_read_result_reports_unavailable_without_crashing_callers(self):
        from database import patient_profile as pp_db

        with patch.object(pp_db, "get_worksheet", return_value=None):
            result = pp_db.read_patient_profile_result("U1")

        self.assertFalse(result.available)
        self.assertIsNone(result.profile)
        self.assertIsNone(pp_db.read_patient_profile("U1"))

    def test_invalid_phone_is_not_persisted_and_status_stays_incomplete(self):
        from database import patient_profile as pp_db

        captured = {}

        class _Sheet:
            def get_all_values(self):
                return [pp_db.HEADERS]

            def update(self, *_args, **_kwargs):
                pass

            def append_row(self, row, value_input_option=None):
                captured["row"] = row

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U1", {
                "first_name": "สมชาย",
                "last_name": "ใจดี",
                "hn": "HN001",
                "phone": "021234567",
                "consent_granted": True,
            })

        self.assertTrue(ok)
        self.assertEqual(captured["row"][10], "")
        self.assertEqual(captured["row"][11], "incomplete")

    def test_short_legacy_row_is_padded_and_future_value_survives_update(self):
        from database import patient_profile as pp_db

        captured = {}

        class _Sheet:
            def get_all_values(self):
                return [
                    ["User_ID", "First_Name", "Future_Field"],
                    ["U1", "เดิม"],
                ]

            def update(self, target_range, values, value_input_option=None):
                captured.setdefault("updates", []).append((target_range, values))

            def append_row(self, *_args, **_kwargs):
                raise AssertionError("append not expected")

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U1", {"last_name": "ใหม่"})

        self.assertTrue(ok)
        headers = captured["updates"][0][1][0]
        row = captured["updates"][1][1][0]
        self.assertEqual(row[headers.index("First_Name")], "เดิม")
        self.assertEqual(row[headers.index("Last_Name")], "ใหม่")
        self.assertEqual(row[headers.index("Future_Field")], "")

    def test_status_and_timestamps_are_derived_not_forced_by_caller(self):
        from database import patient_profile as pp_db

        captured = {}

        class _Sheet:
            def get_all_values(self):
                return [pp_db.HEADERS]

            def append_row(self, row, value_input_option=None):
                captured["row"] = row

            def update(self, *_args, **_kwargs):
                pass

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U1", {
                "first_name": "สมชาย",
                "last_name": "ใจดี",
                "hn": "HN001",
                "phone": "0812345678",
                "registration_status": "registered",
                "registered_at": "2099-01-01",
                "consent_version": "v1",
                "consent_at": "2099-01-01",
            })

        self.assertTrue(ok)
        self.assertEqual(captured["row"][11], "incomplete")
        self.assertEqual(captured["row"][12], "")
        self.assertEqual(captured["row"][13], "")
        self.assertEqual(captured["row"][14], "")

    def test_registration_state_transitions_preserve_history(self):
        from database import patient_profile as pp_db

        values = [
            pp_db.HEADERS,
            [
                "U1", "", "", "", "", "", "2026-01-01 08:00:00",
                "สมชาย", "ใจดี", "HN001", "0812345678",
                "incomplete", "", "", "", "",
            ],
        ]
        captured = {}

        class _Sheet:
            def get_all_values(self):
                return values

            def update(self, target_range, rows, value_input_option=None):
                captured.setdefault("updates", []).append(rows[0])
                if target_range.startswith("A2:"):
                    values[1] = rows[0]

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()), \
             patch.object(pp_db, "_now_str", side_effect=[
                 "2026-01-02 09:00:00",
                 "2026-01-02 09:00:01",
                 "2026-01-03 10:00:00",
                 "2026-01-04 11:00:00",
             ]):
            self.assertTrue(pp_db.upsert_patient_profile("U1", {"consent_granted": True}))
            first_registered_at = values[1][12]
            first_consent_at = values[1][14]
            self.assertEqual(values[1][11], "registered")
            self.assertTrue(pp_db.upsert_patient_profile("U1", {"first_name": "สมชาย"}))
            self.assertEqual(values[1][12], first_registered_at)
            self.assertEqual(values[1][14], first_consent_at)
            self.assertTrue(pp_db.upsert_patient_profile("U1", {"phone": ""}))
            self.assertEqual(values[1][11], "incomplete")
            self.assertEqual(values[1][12], first_registered_at)
            self.assertEqual(values[1][14], first_consent_at)

    def test_outdated_consent_version_keeps_profile_incomplete(self):
        from database import patient_profile as pp_db

        class _Sheet:
            def get_all_values(self):
                return [
                    pp_db.HEADERS,
                    [
                        "U1", "", "", "", "", "", "2026-01-01",
                        "สมชาย", "ใจดี", "HN001", "0812345678",
                        "registered", "2026-01-01", "old", "2026-01-01", "",
                    ],
                ]

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            profile = pp_db.read_patient_profile("U1")

        self.assertEqual(profile["registration_status"], "incomplete")

    def test_demographics_are_not_required_for_registration(self):
        from database import patient_profile as pp_db

        class _Sheet:
            def get_all_values(self):
                return [
                    pp_db.HEADERS,
                    [
                        "U1", "", "", "", "", "", "2026-01-01",
                        "สมชาย", "ใจดี", "HN001", "0812345678",
                        "registered", "2026-01-01", "v1", "2026-01-01", "",
                    ],
                ]

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            profile = pp_db.read_patient_profile("U1")

        self.assertEqual(profile["registration_status"], "registered")


class PatientRegistrationServiceTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_registration_merge_keeps_valid_fields_and_rejects_bad_phone(self):
        from services.patient_profile import prepare_registration_update

        update = prepare_registration_update(
            {},
            {
                "first_name": "สมชาย",
                "last_name": "ใจดี",
                "hn": "hn001",
                "phone": "021234567",
            },
        )

        self.assertEqual(update.profile["first_name"], "สมชาย")
        self.assertEqual(update.profile["hn"], "HN001")
        self.assertNotIn("phone", update.profile)
        self.assertEqual(update.invalid_fields, ["phone"])
        self.assertEqual(update.missing_fields[0], "phone")

    def test_registration_complete_requires_explicit_consent(self):
        from services.patient_profile import prepare_registration_update

        update = prepare_registration_update(
            {},
            {
                "first_name": "สมชาย",
                "last_name": "ใจดี",
                "hn": "HN001",
                "phone": "+66812345678",
                "consent": "ยินยอม",
            },
        )

        self.assertEqual(update.profile["phone"], "0812345678")
        self.assertTrue(update.profile["consent_granted"])
        self.assertEqual(update.missing_fields, [])

    def test_consent_does_not_come_from_query_text_or_ambiguous_okay(self):
        from services.patient_profile import extract_explicit_consent, parse_consent_value

        self.assertIsNone(extract_explicit_consent({"query_text": "yes ตกลง"}))
        self.assertIsNone(parse_consent_value("ok"))
        self.assertIsNone(parse_consent_value("okay"))
        self.assertTrue(extract_explicit_consent({"consent": "ตกลง"}))

    def test_registration_gate_fails_open_when_storage_unavailable(self):
        from services.patient_profile import should_prompt_registration
        from database.patient_profile import PatientProfileReadResult

        with patch("services.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(False, None)):
            decision = should_prompt_registration("U1")

        self.assertFalse(decision.prompt)
        self.assertEqual(decision.reason, "storage_unavailable")

    def test_last_active_missing_or_unavailable_profile_does_not_write(self):
        from database.patient_profile import PatientProfileReadResult
        from services.patient_profile import touch_last_active

        for result in (
            PatientProfileReadResult(True, None),
            PatientProfileReadResult(False, None),
        ):
            with self.subTest(result=result), \
                 patch("services.patient_profile.read_patient_profile_result", return_value=result), \
                 patch("services.patient_profile.upsert_patient_profile") as upsert:
                self.assertFalse(touch_last_active("U1"))
                upsert.assert_not_called()

    def test_last_active_recent_timestamp_sets_throttle_without_write(self):
        from database.patient_profile import PatientProfileReadResult
        from services.cache import ttl_cache
        from services.patient_profile import touch_last_active

        now = datetime(2026, 1, 2, 12, 0, 0)
        recent = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with patch("services.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(True, {"last_active_at": recent})), \
             patch("services.patient_profile.upsert_patient_profile") as upsert:
            self.assertFalse(touch_last_active("U1", now=now))

        upsert.assert_not_called()
        self.assertTrue(ttl_cache.get("profile:last-active:v1:U1"))

    def test_last_active_stale_updates_once_and_throttles_second_call(self):
        from database.patient_profile import PatientProfileReadResult
        from services.patient_profile import touch_last_active

        now = datetime(2026, 1, 2, 12, 0, 0)
        stale = (now - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
        read = Mock(return_value=PatientProfileReadResult(True, {"last_active_at": stale}))
        upsert = Mock(return_value=True)
        with patch("services.patient_profile.read_patient_profile_result", read), \
             patch("services.patient_profile.upsert_patient_profile", upsert):
            self.assertTrue(touch_last_active("U1", now=now))
            self.assertFalse(touch_last_active("U1", now=now + timedelta(minutes=1)))

        self.assertEqual(read.call_count, 1)
        self.assertEqual(upsert.call_count, 1)

    def test_last_active_failure_returns_false_without_raising(self):
        from database.patient_profile import PatientProfileReadResult
        from services.patient_profile import touch_last_active

        with patch("services.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(True, {"last_active_at": ""})), \
             patch("services.patient_profile.upsert_patient_profile", side_effect=RuntimeError("boom")):
            self.assertFalse(touch_last_active("U1"))


class PatientRegistrationWebhookTests(unittest.TestCase):

    def setUp(self):
        from flask import Flask
        self.app = Flask(__name__)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_register_patient_persists_phone_then_asks_for_consent(self):
        from routes.webhook import handle_patient_identity
        from database.patient_profile import PatientProfileReadResult

        captured = {}

        def _upsert(user_id, profile):
            captured["profile"] = profile
            return True

        with patch("database.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(True, {})), \
             patch("database.patient_profile.upsert_patient_profile", side_effect=_upsert), \
             patch("services.dashboard_readers.invalidate_dashboard_cache", return_value=0):
            response, status = handle_patient_identity("U1", {
                "first_name": "สมชาย",
                "last_name": "ใจดี",
                "hn": "HN001",
                "phone": "081-234-5678",
            }, "")

        self.assertEqual(status, 200)
        self.assertEqual(captured["profile"]["phone"], "0812345678")
        self.assertIn("last_active_at", captured["profile"])
        self.assertIn("ยินยอม", response.get_json()["fulfillmentText"])

    def test_registration_storage_unavailable_returns_unavailable_and_does_not_write(self):
        from routes.webhook import handle_patient_identity
        from database.patient_profile import PatientProfileReadResult

        with patch("database.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(False, None)), \
             patch("database.patient_profile.upsert_patient_profile") as upsert:
            response, status = handle_patient_identity("U1", {
                "first_name": "สมชาย",
                "phone": "0812345678",
            }, "")

        self.assertEqual(status, 200)
        self.assertIn("ระบบบันทึกข้อมูลขัดข้อง", response.get_json()["fulfillmentText"])
        upsert.assert_not_called()

    def test_missing_profile_available_storage_starts_registration(self):
        from routes.webhook import handle_patient_identity
        from database.patient_profile import PatientProfileReadResult

        with patch("database.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(True, None)):
            response, status = handle_patient_identity("U1", {}, "")

        self.assertEqual(status, 200)
        self.assertIn("ชื่อจริง", response.get_json()["fulfillmentText"])

    def test_existing_profile_resumes_from_stored_fields(self):
        from routes.webhook import handle_patient_identity
        from database.patient_profile import PatientProfileReadResult

        with patch("database.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(True, {
                       "first_name": "สมชาย",
                       "last_name": "ใจดี",
                       "hn": "HN001",
                   })):
            response, status = handle_patient_identity("U1", {}, "")

        self.assertEqual(status, 200)
        self.assertIn("เบอร์โทรศัพท์", response.get_json()["fulfillmentText"])

    def test_registration_turn_writes_at_most_once(self):
        from routes.webhook import handle_patient_identity
        from database.patient_profile import PatientProfileReadResult

        for params in (
            {"first_name": "สมชาย"},
            {
                "first_name": "สมชาย", "last_name": "ใจดี", "hn": "HN001",
                "phone": "0812345678", "consent": "ยินยอม",
            },
        ):
            with self.subTest(params=params), \
                 patch("database.patient_profile.read_patient_profile_result",
                       return_value=PatientProfileReadResult(True, {})), \
                 patch("database.patient_profile.upsert_patient_profile", return_value=True) as upsert, \
                 patch("services.patient_profile.touch_last_active") as touch, \
                 patch("services.dashboard_readers.invalidate_dashboard_cache", return_value=0):
                handle_patient_identity("U1", params, "")

            self.assertEqual(upsert.call_count, 1)
            touch.assert_not_called()

    def test_registration_gate_blocks_nonurgent_intent_when_enabled(self):
        from routes.webhook import _dispatch_intent
        from database.patient_profile import PatientProfileReadResult

        with patch("config.PATIENT_REGISTRATION_GATE_ENABLED", True), \
             patch("services.patient_profile.read_patient_profile_result",
                   return_value=PatientProfileReadResult(True, {
                       "first_name": "สมชาย",
                       "registration_status": "incomplete",
                   })):
            response, status = _dispatch_intent("AssessRisk", "U1", {}, "")

        self.assertEqual(status, 200)
        self.assertIn("ลงทะเบียน", response.get_json()["fulfillmentText"])

    def test_register_patient_alias_dispatches_registration_handler(self):
        from routes.webhook import _dispatch_intent

        with patch("routes.webhook.handle_patient_identity",
                   return_value=("ok", 200)) as handler:
            response = _dispatch_intent("RegisterPatient", "U1", {}, "")

        self.assertEqual(response, ("ok", 200))
        handler.assert_called_once()

    def test_dispatch_gate_default_off_and_registered_or_unavailable_run_handler(self):
        from routes.webhook import _dispatch_intent
        from database.patient_profile import PatientProfileReadResult

        cases = [
            (False, PatientProfileReadResult(True, {"registration_status": "incomplete"})),
            (True, PatientProfileReadResult(True, {
                "first_name": "สมชาย", "last_name": "ใจดี", "hn": "HN001",
                "phone": "0812345678", "consent_version": "v1", "consent_at": "2026-01-01",
            })),
            (True, PatientProfileReadResult(False, None)),
        ]
        for enabled, result in cases:
            with self.subTest(enabled=enabled, result=result), \
                 patch("config.PATIENT_REGISTRATION_GATE_ENABLED", enabled), \
                 patch("services.patient_profile.read_patient_profile_result", return_value=result), \
                 patch("routes.webhook.handle_assess_risk",
                       return_value=("ran", 200)) as handler:
                self.assertEqual(_dispatch_intent("AssessRisk", "U1", {}, ""), ("ran", 200))
                handler.assert_called_once()

    def test_gate_prompts_all_nonurgent_identity_dependent_intents(self):
        from routes.webhook import _dispatch_intent
        from database.patient_profile import PatientProfileReadResult

        for intent in [
            "AssessRisk", "AssessPersonalRisk", "RequestAppointment",
            "GetFollowUpSummary", "RecommendKnowledge",
        ]:
            with self.subTest(intent=intent), \
                 patch("config.PATIENT_REGISTRATION_GATE_ENABLED", True), \
                 patch("services.patient_profile.read_patient_profile_result",
                       return_value=PatientProfileReadResult(True, {"first_name": "สมชาย"})), \
                 patch("routes.webhook.handle_assess_risk") as assess, \
                 patch("routes.webhook.handle_request_appointment") as appointment, \
                 patch("routes.webhook.handle_get_followup_summary") as followup, \
                 patch("routes.webhook.handle_recommend_knowledge") as recommend:
                response, status = _dispatch_intent(intent, "U1", {}, "")
                self.assertEqual(status, 200)
                self.assertIn("ลงทะเบียน", response.get_json()["fulfillmentText"])
                assess.assert_not_called()
                appointment.assert_not_called()
                followup.assert_not_called()
                recommend.assert_not_called()

    def test_gate_bypass_intents_and_untracked_intents_do_not_touch_activity(self):
        from routes.webhook import _dispatch_intent

        handlers = {
            "ReportSymptoms": "handle_report_symptoms",
            "FreeTextSymptom": "handle_free_text_symptom",
            "ContactNurse": "handle_contact_nurse",
            "AfterHoursChoice": "handle_after_hours_choice",
            "CancelConsultation": "handle_cancel_consultation",
            "PatientIdentity": "handle_patient_identity",
            "UpdatePatientIdentity": "handle_patient_identity",
            "RegisterPatient": "handle_patient_identity",
            "GetKnowledge": "handle_get_knowledge",
            "GetGroupID": "handle_get_group_id",
            "UnknownThing": "handle_unknown_intent",
        }
        for intent, handler_name in handlers.items():
            return_value = {"message": "ok"} if intent == "AfterHoursChoice" else ("ok", 200)
            with self.subTest(intent=intent), \
                 patch("config.PATIENT_REGISTRATION_GATE_ENABLED", True), \
                 patch(f"routes.webhook.{handler_name}", return_value=return_value), \
                 patch("services.patient_profile.touch_last_active") as touch:
                _dispatch_intent(intent, "U1", {}, "")
                if intent in {"GetGroupID", "UnknownThing", "PatientIdentity", "UpdatePatientIdentity", "RegisterPatient"}:
                    touch.assert_not_called()

    def test_tracked_normal_intent_touches_after_handler(self):
        from routes.webhook import _dispatch_intent

        with patch("routes.webhook.handle_get_knowledge", return_value=("ok", 200)), \
             patch("services.patient_profile.touch_last_active") as touch:
            _dispatch_intent("GetKnowledge", "U1", {}, "")

        touch.assert_called_once_with("U1")

    def test_line_media_flows_do_not_touch_activity(self):
        from routes.webhook import handle_line_image_event, register_routes
        from flask import Flask

        with patch("routes.webhook._touch_activity") as touch, \
             patch("services.notification.download_line_content", return_value=b"img"), \
             patch("services.notification.reply_line_message", return_value=True), \
             patch("services.notification.send_line_push", return_value=True), \
             patch("services.wound_analysis.analyze_wound_image",
                   return_value={"severity": "low", "observations": [], "advice": "", "confidence": 1.0}), \
             patch("database.wound_logs.save_wound_analysis", return_value=True):
            handle_line_image_event({
                "source": {"userId": "U123456789"},
                "replyToken": "R",
                "message": {"id": "M"},
            })
        touch.assert_not_called()

        app = Flask(__name__)
        register_routes(app)
        with app.test_client() as client, \
             patch("services.voice.handle_voice_event") as voice, \
             patch("routes.webhook._touch_activity") as touch:
            client.post("/line/webhook", json={"events": [{
                "type": "message",
                "source": {"userId": "U123456789"},
                "message": {"type": "audio", "id": "A"},
            }]})
        voice.assert_called_once()
        touch.assert_not_called()

    def test_debug_logging_never_logs_registration_param_values(self):
        from app import create_app

        app = create_app()
        client = app.test_client()
        payload = {
            "session": "projects/p/agent/sessions/U12345678901234567890",
            "queryResult": {
                "queryText": "ลงทะเบียน",
                "intent": {"displayName": "RegisterPatient"},
                "parameters": {
                    "first_name": "สมชาย",
                    "last_name": "ใจดี",
                    "hn": "HN777",
                    "phone": "0812345678",
                    "consent": "ยินยอม",
                },
            },
        }
        with patch("routes.webhook.DEBUG", True), \
             patch("routes.webhook.handle_patient_identity", return_value=("ok", 200)), \
             self.assertLogs("routes.webhook", level="INFO") as logs:
            client.post("/webhook", json=payload)

        joined = "\n".join(logs.output)
        self.assertIn("ParamKeys", joined)
        self.assertIn("phone", joined)
        for value in ("สมชาย", "ใจดี", "HN777", "0812345678", "ยินยอม"):
            self.assertNotIn(value, joined)


class PatientRegistrationDashboardActionTests(unittest.TestCase):

    def test_dashboard_rejects_invalid_phone_without_write(self):
        from services.dashboard_actions import update_patient_identity

        with patch("database.patient_profile.read_patient_profile") as read, \
             patch("database.patient_profile.upsert_patient_profile") as upsert:
            result = update_patient_identity(
                "U1",
                "nurse_kwan",
                {
                    "first_name": "สมชาย",
                    "last_name": "ใจดี",
                    "hn": "HN001",
                    "phone": "021234567",
                },
            )

        self.assertFalse(result.ok)
        read.assert_not_called()
        upsert.assert_not_called()

    def test_dashboard_omitted_phone_preserves_existing_phone(self):
        from services.dashboard_actions import update_patient_identity

        captured = {}

        def _upsert(_user_id, profile):
            captured["profile"] = profile
            return True

        with patch("database.patient_profile.read_patient_profile",
                   return_value={"phone": "0812345678"}), \
             patch("database.patient_profile.upsert_patient_profile",
                   side_effect=_upsert), \
             patch("services.dashboard_actions.invalidate_dashboard_cache", return_value=0):
            result = update_patient_identity(
                "U1",
                "nurse_kwan",
                {"first_name": "สมชาย", "last_name": "ใจดี", "hn": "HN001"},
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured["profile"]["phone"], "0812345678")


if __name__ == "__main__":
    unittest.main(verbosity=2)
