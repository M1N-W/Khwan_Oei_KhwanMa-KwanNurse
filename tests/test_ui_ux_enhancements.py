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

if __name__ == "__main__":
    unittest.main()
