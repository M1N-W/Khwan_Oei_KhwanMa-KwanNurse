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
            self.assertEqual(len(items), 4)
            self.assertEqual(items[0]["action"]["label"], "🟢 0-2 (ปวดน้อย)")
            self.assertEqual(items[0]["action"]["text"], "2")
            self.assertEqual(items[1]["action"]["label"], "🟡 3-5 (ปวดปานกลาง)")
            self.assertEqual(items[1]["action"]["text"], "5")
            self.assertEqual(items[2]["action"]["label"], "🟠 6-7 (ปวดมาก)")
            self.assertEqual(items[2]["action"]["text"], "7")
            self.assertEqual(items[3]["action"]["label"], "🔴 8-10 (ปวดรุนแรง)")
            self.assertEqual(items[3]["action"]["text"], "9")

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

    def test_after_hours_quick_reply_has_exactly_two_items(self):
        """After-hours quick reply must have exactly 2 buttons."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        items = line_payload["quickReply"]["items"]
        self.assertEqual(len(items), 2)

    def test_after_hours_first_button_is_wait(self):
        """First after-hours button: label='⏳ รอเวลาทำการ', text='รอเวลาทำการ'."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        first = line_payload["quickReply"]["items"][0]
        self.assertEqual(first["action"]["label"], "⏳ รอเวลาทำการ")
        self.assertEqual(first["action"]["text"], "รอเวลาทำการ")

    def test_after_hours_second_button_is_emergency(self):
        """Second after-hours button: label='🚨 แจ้งเรื่องฉุกเฉิน', text='แจ้งเรื่องฉุกเฉิน'."""
        data = self._call_contact_nurse_after_hours()
        msgs = data.get("fulfillmentMessages", [])
        line_payload = next(
            (m["payload"]["line"] for m in msgs if "payload" in m and "line" in m["payload"]),
            None,
        )
        second = line_payload["quickReply"]["items"][1]
        self.assertEqual(second["action"]["label"], "🚨 แจ้งเรื่องฉุกเฉิน")
        self.assertEqual(second["action"]["text"], "แจ้งเรื่องฉุกเฉิน")

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
             patch("config.GSPREAD_CREDENTIALS", "mock_creds"):
            response = client.get("/")
            data = json.loads(response.data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["service"], "KwanNurse-Bot v5.0")
            self.assertEqual(data["version"], "5.0 - Complete (UX/UI Polish)")
            self.assertIn("diagnostics", data)
            self.assertTrue(data["diagnostics"]["config_ok"])


if __name__ == "__main__":
    unittest.main()
