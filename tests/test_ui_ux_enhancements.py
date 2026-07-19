# -*- coding: utf-8 -*-
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from services import line_message
from services import reminder as service_reminder

class TestUIUXEnhancements(unittest.TestCase):
    def test_build_daily_checkin_reminder(self):
        flex = line_message.build_daily_checkin_reminder()
        self.assertEqual(flex["type"], "flex")
        self.assertIn("🔔 ได้เวลารายงานอาการประจำวันแล้วค่ะ", flex["altText"])
        contents = flex["contents"]
        self.assertEqual(contents["type"], "bubble")
        self.assertEqual(contents["header"]["backgroundColor"], "#2E7D32")
        # Verify CTA button is present
        footer = contents["footer"]
        button = footer["contents"][0]
        self.assertEqual(button["action"]["type"], "message")
        self.assertEqual(button["action"]["label"], "📝 รายงานอาการตอนนี้")
        self.assertEqual(button["action"]["text"], "รายงานอาการ")

    @patch("services.reminder.ENABLE_RICH_MESSAGES", True)
    @patch("services.line_message.push_rich_message")
    @patch("services.reminder.send_line_push")
    @patch("services.reminder.save_reminder_sent")
    def test_send_reminder_rich_enabled_day3(self, mock_save_sent, mock_send_push, mock_push_rich):
        mock_push_rich.return_value = True
        res = service_reminder.send_reminder("U12345", "day3")
        self.assertTrue(res)
        mock_push_rich.assert_called_once()
        # Verify first argument is a list containing the daily check-in flex message
        flex_arg = mock_push_rich.call_args[0][0]
        self.assertEqual(flex_arg[0]["type"], "flex")
        self.assertIn("🔔 ได้เวลารายงานอาการประจำวันแล้วค่ะ", flex_arg[0]["altText"])
        mock_send_push.assert_not_called()

    @patch("services.reminder.ENABLE_RICH_MESSAGES", False)
    @patch("services.line_message.push_rich_message")
    @patch("services.reminder.send_line_push")
    @patch("services.reminder.save_reminder_sent")
    def test_send_reminder_rich_disabled_day3(self, mock_save_sent, mock_send_push, mock_push_rich):
        mock_send_push.return_value = True
        res = service_reminder.send_reminder("U12345", "day3")
        self.assertTrue(res)
        mock_send_push.assert_called_once()
        self.assertIn("แผลหายดีไหมคะ", mock_send_push.call_args[0][0])
        mock_push_rich.assert_not_called()

    @patch("services.reminder.ENABLE_RICH_MESSAGES", True)
    @patch("services.line_message.push_rich_message")
    @patch("services.reminder.send_line_push")
    @patch("services.reminder.save_reminder_sent")
    def test_send_reminder_rich_enabled_day7(self, mock_save_sent, mock_send_push, mock_push_rich):
        mock_send_push.return_value = True
        res = service_reminder.send_reminder("U12345", "day7")
        self.assertTrue(res)
        mock_send_push.assert_called_once()
        mock_push_rich.assert_not_called()

    @patch("services.reminder.ENABLE_RICH_MESSAGES", True)
    @patch("services.line_message.push_rich_message")
    @patch("services.reminder.send_line_push")
    def test_dispatch_single_rich_enabled_day3(self, mock_send_push, mock_push_rich):
        mock_push_rich.return_value = True
        mock_claim = MagicMock(return_value=True)
        mock_update = MagicMock()
        
        reminder = {
            "User_ID": "U12345",
            "Reminder_Type": "day3",
            "Row_Num": 2,
            "Retry_Count": 0
        }
        
        service_reminder._dispatch_single(reminder, mock_claim, mock_send_push, mock_update)
        
        mock_push_rich.assert_called_once()
        flex_arg = mock_push_rich.call_args[0][0]
        self.assertEqual(flex_arg[0]["type"], "flex")
        mock_send_push.assert_not_called()
        mock_update.assert_called_once_with("U12345", "day3", 2, "sent")

    @patch("config.ENABLE_RICH_MESSAGES", True)
    def test_symptom_report_quick_replies(self):
        from routes.webhook.handlers.symptoms import handle_report_symptoms
        from flask import Flask
        import json

        app = Flask("test_app")
        with app.app_context():
            # 1. Test missing pain_score — ask must mention ONLY pain, not other missing fields.
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "",
                "wound_status": "แผลแห้งดี",
                "fever_check": "ไม่มีไข้",
                "mobility_status": "เดินได้ปกติ"
            })
            data = json.loads(response[0].data)
            # Focused ask text must reference only the pain slot
            self.assertIn("ระดับความปวด", data["fulfillmentText"])
            self.assertNotIn("สภาพแผล", data["fulfillmentText"])
            line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
            self.assertIn("quickReply", line_payload)
            items = line_payload["quickReply"]["items"]
            self.assertEqual(len(items), 5)
            self.assertEqual(items[0]["action"]["label"], "🟢 1 (ปวดน้อย)")
            self.assertEqual(items[0]["action"]["text"], "1")
            self.assertEqual(items[1]["action"]["label"], "🟡 2 (ปวดเล็กน้อย)")
            self.assertEqual(items[1]["action"]["text"], "2")
            self.assertEqual(items[2]["action"]["label"], "🟠 3 (ปวดปานกลาง)")
            self.assertEqual(items[2]["action"]["text"], "3")
            self.assertEqual(items[3]["action"]["label"], "🔴 4 (ปวดมาก)")
            self.assertEqual(items[3]["action"]["text"], "4")
            self.assertEqual(items[4]["action"]["label"], "🚨 5 (ปวดรุนแรง)")
            self.assertEqual(items[4]["action"]["text"], "5")

            # 2. Test missing wound_status — ask must mention ONLY wound.
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "5",
                "wound_status": "",
                "fever_check": "ไม่มีไข้",
                "mobility_status": "เดินได้ปกติ"
            })
            data = json.loads(response[0].data)
            self.assertIn("สภาพแผล", data["fulfillmentText"])
            self.assertNotIn("อาการไข้", data["fulfillmentText"])
            line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
            self.assertIn("quickReply", line_payload)
            items = line_payload["quickReply"]["items"]
            self.assertEqual(len(items), 3)
            self.assertEqual(items[0]["action"]["label"], "🟢 แผลแห้งดี")
            self.assertEqual(items[0]["action"]["text"], "แผลแห้งดี")
            self.assertEqual(items[1]["action"]["label"], "🟡 แผลซึม/แดง")
            self.assertEqual(items[1]["action"]["text"], "แผลแดงซึม")
            self.assertEqual(items[2]["action"]["label"], "🔴 แผลบวม/มีหนอง")
            self.assertEqual(items[2]["action"]["text"], "แผลบวมหนอง")

            # 3. Test missing fever_check — ask must mention ONLY fever.
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "5",
                "wound_status": "แผลแห้งดี",
                "fever_check": "",
                "mobility_status": "เดินได้ปกติ"
            })
            data = json.loads(response[0].data)
            self.assertIn("อาการไข้", data["fulfillmentText"])
            self.assertNotIn("การเคลื่อนไหว", data["fulfillmentText"])
            line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
            self.assertIn("quickReply", line_payload)
            items = line_payload["quickReply"]["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["action"]["label"], "🟢 ไม่มีไข้")
            self.assertEqual(items[0]["action"]["text"], "ไม่มีไข้")
            self.assertEqual(items[1]["action"]["label"], "🔴 มีไข้ตัวร้อน")
            self.assertEqual(items[1]["action"]["text"], "มีไข้")

            # 4. Test missing mobility_status — ask must mention ONLY mobility.
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "5",
                "wound_status": "แผลแห้งดี",
                "fever_check": "ไม่มีไข้",
                "mobility_status": ""
            })
            data = json.loads(response[0].data)
            self.assertIn("การเคลื่อนไหว", data["fulfillmentText"])
            self.assertNotIn("อาการไข้", data["fulfillmentText"])
            line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
            self.assertIn("quickReply", line_payload)
            items = line_payload["quickReply"]["items"]
            self.assertEqual(len(items), 3)
            self.assertEqual(items[0]["action"]["label"], "🟢 เดินได้ปกติ")
            self.assertEqual(items[0]["action"]["text"], "เดินได้ปกติ")
            self.assertEqual(items[1]["action"]["label"], "🟡 ต้องพยุงเดิน")
            self.assertEqual(items[1]["action"]["text"], "ต้องพยุง")
            self.assertEqual(items[2]["action"]["label"], "🔴 เดินไม่ได้เลย")
            self.assertEqual(items[2]["action"]["text"], "เดินไม่ได้")


class TestKBNavigationQuickReplies(unittest.TestCase):
    """Task 3: KB Navigation Quick Replies appended to every educational guide."""

    def _call_get_knowledge(self, topic_param, mock_guide_text="เนื้อหาคู่มือทดสอบ"):
        """Helper: call handle_get_knowledge with ENABLE_RICH_MESSAGES=True."""
        from flask import Flask
        from routes.webhook.handlers.fallback import handle_get_knowledge
        import json

        app = Flask("test_kb_nav")
        with app.app_context():
            with patch("config.ENABLE_RICH_MESSAGES", True), \
                 patch("routes.webhook.handlers.fallback.save_education_view"), \
                 patch("routes.webhook.save_education_view"), \
                 patch("routes.webhook.get_wound_care_guide", return_value=mock_guide_text), \
                 patch("routes.webhook.get_physical_therapy_guide", return_value=mock_guide_text), \
                 patch("routes.webhook.get_dvt_prevention_guide", return_value=mock_guide_text), \
                 patch("routes.webhook.get_medication_guide", return_value=mock_guide_text), \
                 patch("routes.webhook.get_warning_signs_guide", return_value=mock_guide_text):
                response = handle_get_knowledge("U_TEST", {"topic": topic_param})
                data = json.loads(response[0].data)
        return data

    def test_guide_response_has_quick_reply_block(self):
        """A guide response must contain a quickReply block in the LINE payload."""
        data = self._call_get_knowledge("wound_care")
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        self.assertIsNotNone(line_payload, "Expected a LINE payload in fulfillmentMessages")
        self.assertIn("quickReply", line_payload)

    def test_guide_response_has_exactly_two_nav_buttons(self):
        """The quickReply block must contain exactly 2 navigation items."""
        data = self._call_get_knowledge("กายภาพบำบัด")
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        items = line_payload["quickReply"]["items"]
        self.assertEqual(len(items), 2)

    def test_first_nav_button_is_knowledge_menu(self):
        """First quick reply: label='📚 เมนูความรู้หลัก', text='ความรู้'."""
        data = self._call_get_knowledge("ลิ่มเลือด")
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        first = line_payload["quickReply"]["items"][0]
        self.assertEqual(first["action"]["label"], "📚 เมนูความรู้หลัก")
        self.assertEqual(first["action"]["text"], "ความรู้")

    def test_second_nav_button_is_consult_nurse(self):
        """Second quick reply: label='🏥 ปรึกษาพยาบาล', text='ปรึกษาพยาบาล'."""
        data = self._call_get_knowledge("medication")
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        second = line_payload["quickReply"]["items"][1]
        self.assertEqual(second["action"]["label"], "🏥 ปรึกษาพยาบาล")
        self.assertEqual(second["action"]["text"], "ปรึกษาพยาบาล")

    def test_knowledge_menu_response_has_no_nav_quick_replies(self):
        """The knowledge MENU (not a guide) should NOT get the nav quick replies."""
        from flask import Flask
        from routes.webhook.handlers.fallback import handle_get_knowledge
        import json

        app = Flask("test_kb_menu")
        with app.app_context():
            with patch("config.ENABLE_RICH_MESSAGES", True), \
                 patch("services.get_knowledge_menu", return_value="เมนูความรู้"):
                response = handle_get_knowledge("U_TEST", {"topic": ""}, query_text="ความรู้")
                data = json.loads(response[0].data)
        # Menu path just returns fulfillmentText — no rich payload
        self.assertIn("fulfillmentText", data)
        # fulfillmentMessages with nav quick replies must NOT be present
        msgs = data.get("fulfillmentMessages", [])
        nav_labels = {"📚 เมนูความรู้หลัก", "🏥 ปรึกษาพยาบาล"}
        for m in msgs:
            line_payload = m.get("payload", {}).get("line", {})
            for item in line_payload.get("quickReply", {}).get("items", []):
                self.assertNotIn(item["action"]["label"], nav_labels)

    def test_guide_fulfillment_text_unchanged(self):
        """The fulfillmentText must still be the guide text itself (unchanged content)."""
        guide_text = "คู่มือดูแลแผลฉบับสมบูรณ์"
        data = self._call_get_knowledge("wound_care", mock_guide_text=guide_text)
        self.assertEqual(data["fulfillmentText"], guide_text)


class TestAfterHoursQuickReplies(unittest.TestCase):
    """Task 4A: After-hours prompt must include quick reply buttons."""

    def _call_contact_nurse_after_hours(self):
        """
        Call handle_contact_nurse with is_office_hours() returning False and
        no category param so it falls into the after-hours branch.
        """
        from flask import Flask
        from routes.webhook.handlers.fallback import handle_contact_nurse
        import json

        app = Flask("test_after_hours_qr")
        with app.app_context():
            with patch("config.ENABLE_RICH_MESSAGES", True), \
                 patch("routes.webhook.handlers.fallback.is_office_hours", return_value=False), \
                 patch("routes.webhook.handlers.fallback.parse_category_choice", return_value=None), \
                 patch("routes.webhook.handlers.fallback.get_category_menu", return_value="เมนูหมวดหมู่"), \
                 patch("routes.webhook.handlers.fallback.start_teleconsult"):
                response = handle_contact_nurse("U_TEST", {}, "")
                data = json.loads(response[0].data)
        return data

    def test_after_hours_response_has_quick_reply_block(self):
        """After-hours prompt must include a LINE quickReply block."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        self.assertIsNotNone(line_payload, "Expected a LINE payload in fulfillmentMessages")
        self.assertIn("quickReply", line_payload)

    def test_after_hours_quick_reply_has_all_visible_category_items(self):
        """After-hours quick reply must match the visible 1-5 category menu."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        items = line_payload["quickReply"]["items"]
        self.assertEqual([item["action"]["text"] for item in items], ["1", "2", "3", "4", "5"])

    def test_after_hours_first_button_is_emergency_category(self):
        """The first quick reply matches category 1 in the visible menu."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        first = line_payload["quickReply"]["items"][0]
        self.assertEqual(first["action"]["label"], "🚨 ฉุกเฉิน")
        self.assertEqual(first["action"]["text"], "1")

    def test_after_hours_last_button_is_contact_nurse_category(self):
        """The last quick reply matches category 5 in the visible menu."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        last = line_payload["quickReply"]["items"][-1]
        self.assertEqual(last["action"]["label"], "👩🏻‍⚕️ ติดต่อพยาบาล")
        self.assertEqual(last["action"]["text"], "5")

    def test_after_hours_choice_wait_resolves_correctly(self):
        """Passing 'รอเวลาทำการ' to handle_after_hours_choice returns success and routes correctly."""
        from routes.webhook.handlers.fallback import handle_after_hours_choice
        from flask import Flask
        import json

        app = Flask("test_after_hours_resolve")
        with app.app_context():
            with patch("services.teleconsult.is_office_hours", return_value=False), \
                 patch("services.teleconsult.get_user_active_session", return_value={"Issue_Type": "med", "Description": "test"}), \
                 patch("services.teleconsult.send_line_push") as m_push:
                response = handle_after_hours_choice("U_TEST", "รอเวลาทำการ")
                data = json.loads(response[0].data)
                self.assertIn("บันทึกคำขอของคุณเรียบร้อยแล้วค่ะ", data["fulfillmentText"])
                m_push.assert_called_once()


class TestSurveyStarRatingQuickReplies(unittest.TestCase):
    """Task 4B: Satisfaction survey message must include 5 star-rating quick reply buttons."""

    def _build_survey(self, milestone_day=7):
        """Call build_survey_message and return the first (text) message object."""
        from services.survey import build_survey_message
        messages = build_survey_message("https://example.com/survey", milestone_day)
        # The rating-question message should be the last item (a text message with quickReply)
        return messages

    def test_survey_message_contains_quick_reply_message(self):
        """build_survey_message must return at least one message with a quickReply block."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        self.assertIsNotNone(rating_msg, "Expected a text message with quickReply in survey messages")

    def test_survey_quick_reply_has_five_stars(self):
        """The quick reply block must have exactly 5 star items."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        items = rating_msg["quickReply"]["items"]
        self.assertEqual(len(items), 5)

    def test_survey_star5_button(self):
        """Star 5 button: label='⭐ 5 (ดีมาก)', text='5'."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        first = rating_msg["quickReply"]["items"][0]
        self.assertEqual(first["action"]["label"], "⭐ 5 (ดีมาก)")
        self.assertEqual(first["action"]["text"], "5")

    def test_survey_star4_button(self):
        """Star 4 button: label='⭐ 4 (ดี)', text='4'."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        second = rating_msg["quickReply"]["items"][1]
        self.assertEqual(second["action"]["label"], "⭐ 4 (ดี)")
        self.assertEqual(second["action"]["text"], "4")

    def test_survey_star3_button(self):
        """Star 3 button: label='⭐ 3 (ปานกลาง)', text='3'."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        third = rating_msg["quickReply"]["items"][2]
        self.assertEqual(third["action"]["label"], "⭐ 3 (ปานกลาง)")
        self.assertEqual(third["action"]["text"], "3")

    def test_survey_star2_button(self):
        """Star 2 button: label='⭐ 2 (พอใช้)', text='2'."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        fourth = rating_msg["quickReply"]["items"][3]
        self.assertEqual(fourth["action"]["label"], "⭐ 2 (พอใช้)")
        self.assertEqual(fourth["action"]["text"], "2")

    def test_survey_star1_button(self):
        """Star 1 button: label='⭐ 1 (ควรปรับปรุง)', text='1'."""
        messages = self._build_survey()
        rating_msg = next(
            (m for m in messages if m.get("type") == "text" and "quickReply" in m),
            None,
        )
        fifth = rating_msg["quickReply"]["items"][4]
        self.assertEqual(fifth["action"]["label"], "⭐ 1 (ควรปรับปรุง)")
        self.assertEqual(fifth["action"]["text"], "1")


class TestHealthCheckDiagnostics(unittest.TestCase):
    def test_health_check_returns_v5_and_diagnostics(self):
        from app import create_app
        import json

        app = create_app()
        client = app.test_client()
        with patch("config.LINE_CHANNEL_ACCESS_TOKEN", "mock_token"), \
             patch("config.NURSE_GROUP_ID", "mock_group"), \
             patch("config.LINE_CHANNEL_SECRET", "mock_line_secret"), \
             patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_dialogflow_token"), \
             patch("config.GSPREAD_CREDENTIALS", "mock_creds"):
            response = client.get("/")
            data = json.loads(response.data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["service"], "ขวัญเอ๋ยขวัญมา-บอท v5.0")
            self.assertEqual(data["version"], "5.0 - Complete (UX/UI Polish)")
            self.assertIn("diagnostics", data)
            self.assertTrue(data["diagnostics"]["config_ok"])


class TestLINEUserIDExtraction(unittest.TestCase):
    def test_extract_line_user_id_data_source_path(self):
        from routes.webhook.handler import _extract_line_user_id
        req = {
            "originalDetectIntentRequest": {
                "source": "line",
                "payload": {
                    "data": {
                        "source": {
                            "userId": "U123456"
                        }
                    }
                }
            }
        }
        self.assertEqual(_extract_line_user_id(req), "U123456")

    def test_extract_line_user_id_payload_source_path(self):
        from routes.webhook.handler import _extract_line_user_id
        req = {
            "originalDetectIntentRequest": {
                "source": "line",
                "payload": {
                    "source": {
                        "userId": "U789012"
                    }
                }
            }
        }
        self.assertEqual(_extract_line_user_id(req), "U789012")

    def test_extract_line_user_id_payload_root_path(self):
        from routes.webhook.handler import _extract_line_user_id
        req = {
            "originalDetectIntentRequest": {
                "source": "line",
                "payload": {
                    "userId": "U345678"
                }
            }
        }
        self.assertEqual(_extract_line_user_id(req), "U345678")

    def test_extract_line_user_id_none_source(self):
        from routes.webhook.handler import _extract_line_user_id
        req = {
            "originalDetectIntentRequest": {
                "source": "line",
                "payload": {
                    "source": None
                }
            }
        }
        self.assertIsNone(_extract_line_user_id(req))

    def test_extract_line_user_id_non_dict_inputs(self):
        from routes.webhook.handler import _extract_line_user_id
        self.assertIsNone(_extract_line_user_id(None))
        self.assertIsNone(_extract_line_user_id([]))
        self.assertIsNone(_extract_line_user_id("string"))


class TestThreadLocalSheetsAndRetry(unittest.TestCase):
    def test_is_transient_includes_ssl_and_maxretry(self):
        from database.retry import is_transient_error
        import urllib3
        import requests

        ssl_exc = requests.exceptions.SSLError("decryption failed or bad record mac")
        max_retry_exc = urllib3.exceptions.MaxRetryError(None, "url", "max retries exceeded")
        self.assertTrue(is_transient_error(ssl_exc))
        self.assertTrue(is_transient_error(max_retry_exc))

    def test_thread_local_isolation(self):
        from database.sheets import _get_local_cache, get_sheet_client
        import threading

        cache = _get_local_cache()
        cache.sheet_client = "main_thread_client"

        other_client = []
        def worker():
            cache_other = _get_local_cache()
            other_client.append(cache_other.sheet_client)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertIsNone(other_client[0])
        self.assertEqual(cache.sheet_client, "main_thread_client")
        # Clean up
        cache.sheet_client = None

    @patch("database.sheets.invalidate_sheet_client")
    def test_retry_invalidates_client_on_ssl_error(self, mock_invalidate):
        from database.retry import retry_sheet_op
        import requests

        call_count = 0
        def failing_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise requests.exceptions.SSLError("decryption failed")
            return "success"

        res = retry_sheet_op(failing_fn, max_attempts=2, base_delay=0.01)
        self.assertEqual(res, "success")
        self.assertEqual(call_count, 2)
        mock_invalidate.assert_called_once()


class TestDeterministicRouter(unittest.TestCase):
    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_deterministic_router_overrides_intent(self):
        from app import create_app
        import json

        app = create_app()
        client = app.test_client()

        with patch("routes.webhook.handler._dispatch_intent") as mock_dispatch:
            mock_dispatch.return_value = "mock_response"
            
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "ลงทะเบียน",
                    "intent": {
                        "displayName": "FreeTextSymptom"
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            mock_dispatch.assert_called_once_with("StartRegistration", "U_TEST", {}, "ลงทะเบียน")

    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_state_machine_intercepts_unregistered_user(self):
        from app import create_app
        from database.patient_profile import PatientProfileReadResult
        import json

        app = create_app()
        client = app.test_client()

        incomplete_profile = {
            "first_name": "",
            "last_name": "",
            "hn": ""
        }

        with patch("routes.webhook.handler._dispatch_intent") as mock_dispatch, \
             patch("database.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, incomplete_profile)):
            
            mock_dispatch.return_value = "mock_response"
            
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "มาวิน",
                    "intent": {
                        "displayName": "RequestAppointment"
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            mock_dispatch.assert_called_once_with("PatientIdentity", "U_TEST", {"first_name": "มาวิน"}, "มาวิน")

    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_state_machine_cancel_resets_profile(self):
        from app import create_app
        from database.patient_profile import PatientProfileReadResult
        import json

        app = create_app()
        client = app.test_client()

        incomplete_profile = {
            "first_name": "มาวิน",
            "last_name": "",
            "hn": ""
        }

        with patch("database.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, incomplete_profile)), \
             patch("database.patient_profile.upsert_patient_profile") as mock_upsert, \
             patch("services.patient_profile.invalidate_profile_cache") as mock_invalidate:
            
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "ยกเลิก",
                    "intent": {
                        "displayName": "CancelConsultation"
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("ยกเลิกการลงทะเบียนเรียบร้อย", response.get_json()["fulfillmentText"])
            mock_upsert.assert_called_once()
            mock_invalidate.assert_called_once()


class TestParameterCoercion(unittest.TestCase):
    def test_coerce_string_handles_varied_types(self):
        from services.patient_profile import _coerce_string

        # Strings are unchanged
        self.assertEqual(_coerce_string("hello"), "hello")
        self.assertEqual(_coerce_string(""), "")
        self.assertEqual(_coerce_string(None), "")

        # Simple dict with standard name key
        self.assertEqual(_coerce_string({"name": "มาวิน"}), "มาวิน")
        # Dict with given-name key
        self.assertEqual(_coerce_string({"given-name": "มาวิน"}), "มาวิน")
        # Nested dict structures from Dialogflow person entity
        self.assertEqual(_coerce_string({"person": {"name": "มาวิน"}}), "มาวิน")
        # Fallback to formatting other values if key not found
        self.assertEqual(_coerce_string({"custom-key": "มาวิน"}), "มาวิน")
        self.assertEqual(_coerce_string(123), "123")

    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_webhook_unpacks_appointment_params(self):
        from app import create_app
        import json

        app = create_app()
        client = app.test_client()

        with patch("routes.webhook.handlers.symptoms.create_appointment", return_value=(True, "นัดหมายสำเร็จ")) as mock_create:
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "ขอนัด",
                    "intent": {
                        "displayName": "RequestAppointment"
                    },
                    "parameters": {
                        "date": "2099-06-28T16:00:00+07:00",
                        "time": "16:00:00",
                        "name": {
                            "name": "มาวิน"
                        },
                        "phone-number": "081-234-5678",
                        "reason": {
                            "symptom": "ตรวจแผล"
                        }
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            self.assertEqual(response.status_code, 200)
            mock_create.assert_called_once_with(
                "U_TEST", "มาวิน", "0812345678", "2099-06-28", "16:00", "ตรวจแผล"
            )

    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_webhook_returns_output_contexts_during_registration(self):
        from app import create_app
        from database.patient_profile import PatientProfileReadResult
        import json

        app = create_app()
        client = app.test_client()

        incomplete_profile = {
            "first_name": "",
            "last_name": "",
            "hn": ""
        }

        with patch("database.patient_profile.read_patient_profile_result", return_value=PatientProfileReadResult(True, incomplete_profile)), \
             patch("database.patient_profile.upsert_patient_profile", return_value=True):
            
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "ลงทะเบียน",
                    "intent": {
                        "displayName": "PatientIdentity"
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("outputContexts", data)
            contexts = data["outputContexts"]
            self.assertEqual(len(contexts), 1)
            self.assertEqual(contexts[0]["name"], "projects/mock/agent/sessions/U_TEST/contexts/registering")
            self.assertEqual(contexts[0]["lifespanCount"], 5)

    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_webhook_maps_patient_identity_fallback_intent(self):
        from app import create_app
        import json

        app = create_app()
        client = app.test_client()

        with patch("routes.webhook.handler._dispatch_intent") as mock_dispatch:
            mock_dispatch.return_value = "mock_response"
            
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "มาวิน",
                    "intent": {
                        "displayName": "PatientIdentity_Fallback"
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            mock_dispatch.assert_called_once_with("PatientIdentity", "U_TEST", {}, "มาวิน")

    @patch("config.DIALOGFLOW_WEBHOOK_TOKEN", "mock_token")
    def test_webhook_maps_patient_identity_input_intent(self):
        from app import create_app
        import json

        app = create_app()
        client = app.test_client()

        with patch("routes.webhook.handler._dispatch_intent") as mock_dispatch:
            mock_dispatch.return_value = "mock_response"
            
            payload = {
                "session": "projects/mock/agent/sessions/U_TEST",
                "queryResult": {
                    "queryText": "มาวิน",
                    "intent": {
                        "displayName": "PatientIdentity_Input"
                    }
                }
            }
            
            response = client.post("/webhook", json=payload, headers={"Authorization": "Bearer mock_token"})
            mock_dispatch.assert_called_once_with("PatientIdentity", "U_TEST", {}, "มาวิน")


if __name__ == "__main__":
    unittest.main()
