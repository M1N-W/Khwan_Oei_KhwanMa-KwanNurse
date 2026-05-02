# -*- coding: utf-8 -*-
"""
Regression tests for reminder persistence hot paths.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("RUN_SCHEDULER", "false")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

from database.reminders import (
    check_no_response_reminders,
    save_reminder_response,
    update_schedule_status,
)


class ReminderDatabaseTests(unittest.TestCase):
    def test_update_schedule_status_uses_batch_update(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes'],
            ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'scheduled', ''],
        ]

        with patch('database.reminders.get_worksheet', return_value=mock_sheet):
            update_schedule_status('u1', 'day3', 'sent')

        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        self.assertEqual(updates[0]['range'], 'F2')
        self.assertEqual(updates[0]['values'], [['sent']])

    def test_check_no_response_reminders_uses_batch_update(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Timestamp', 'User_ID', 'Reminder_Type', 'Status', 'Response_Text', 'Message_Sent', 'Response_Timestamp'],
            ['2026-04-20 08:00:00', 'u2', 'day7', 'sent', '', 'msg', ''],
        ]

        with patch('database.reminders.get_worksheet', return_value=mock_sheet), \
             patch('database.reminders.update_schedule_status') as mock_update_schedule:
            result = check_no_response_reminders()

        self.assertEqual(len(result), 1)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        self.assertEqual(updates[0]['range'], 'D2')
        self.assertEqual(updates[0]['values'], [['no_response']])
        mock_update_schedule.assert_called_once_with('u2', 'day7', 'no_response')

    def test_save_reminder_response_batches_all_field_updates(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Timestamp', 'User_ID', 'Reminder_Type', 'Status', 'Response_Text', 'Message_Sent', 'Response_Timestamp'],
            ['2026-04-22 09:00:00', 'u3', 'day14', 'sent', '', 'msg', ''],
        ]

        with patch('database.reminders.get_worksheet', return_value=mock_sheet), \
             patch('database.reminders.update_schedule_status') as mock_update_schedule:
            success = save_reminder_response('u3', 'day14', 'ดีขึ้นแล้ว')

        self.assertTrue(success)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        update_ranges = {item['range'] for item in updates}
        self.assertIn('D2', update_ranges)
        self.assertIn('E2', update_ranges)
        self.assertIn('G2', update_ranges)
        mock_update_schedule.assert_called_once_with('u3', 'day14', 'responded')


if __name__ == '__main__':
    unittest.main(verbosity=2)
