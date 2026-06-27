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
            # 1. Test missing pain_score
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "",
                "wound_status": "แผลแห้งดี",
                "fever_check": "ไม่มีไข้",
                "mobility_status": "เดินได้ปกติ"
            })
            data = json.loads(response[0].data)
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

            # 2. Test missing wound_status
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "5",
                "wound_status": "",
                "fever_check": "ไม่มีไข้",
                "mobility_status": "เดินได้ปกติ"
            })
            data = json.loads(response[0].data)
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

            # 3. Test missing fever_check
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "5",
                "wound_status": "แผลแห้งดี",
                "fever_check": "",
                "mobility_status": "เดินได้ปกติ"
            })
            data = json.loads(response[0].data)
            line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
            self.assertIn("quickReply", line_payload)
            items = line_payload["quickReply"]["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["action"]["label"], "🟢 ไม่มีไข้")
            self.assertEqual(items[0]["action"]["text"], "ไม่มีไข้")
            self.assertEqual(items[1]["action"]["label"], "🔴 มีไข้ตัวร้อน")
            self.assertEqual(items[1]["action"]["text"], "มีไข้")

            # 4. Test missing mobility_status
            response = handle_report_symptoms("U_TEST", {
                "pain_score": "5",
                "wound_status": "แผลแห้งดี",
                "fever_check": "ไม่มีไข้",
                "mobility_status": ""
            })
            data = json.loads(response[0].data)
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

if __name__ == "__main__":
    unittest.main()
