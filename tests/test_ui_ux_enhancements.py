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


if __name__ == "__main__":
    unittest.main()
