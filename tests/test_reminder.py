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

    def test_verify_schedules_headers_appends_missing_columns(self):
        from database.reminders import _verify_schedules_headers
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes']
        ]
        headers = _verify_schedules_headers(mock_sheet)
        self.assertEqual(len(headers), 12)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        self.assertEqual(updates[0]['range'], 'A1:L1')
        self.assertEqual(updates[0]['values'][0][7], 'Claimed_By')

    def test_get_due_reminders_filters_correctly(self):
        from database.reminders import get_due_reminders
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes', 'Claimed_By', 'Claimed_At', 'Retry_Count', 'Last_Error', 'Last_Attempt_At'],
            ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'scheduled', '', '', '', '0', '', ''], # Row 2: eligible
            ['2026-04-20 09:00:00', 'u2', '2026-04-17', 'day7', '2026-04-25 09:00:00', 'scheduled', '', '', '', '0', '', ''], # Row 3: future, not eligible
            ['2026-04-20 09:00:00', 'u3', '2026-04-17', 'day14', '2026-04-20 09:00:00', 'sent', '', '', '', '0', '', ''],      # Row 4: sent, not eligible
            ['2026-04-20 09:00:00', 'u4', '2026-04-17', 'day30', '2026-04-20 09:00:00', 'failed', '', '', '', '3', '', ''],    # Row 5: max retries reached, not eligible
            ['2026-04-20 09:00:00', 'u5', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'claimed', '', 'w1', '2026-04-23 11:50:00', '0', '', ''], # Row 6: claimed active, not eligible
            ['2026-04-20 09:00:00', 'u6', '2026-04-17', 'day7', '2026-04-20 09:00:00', 'claimed', '', 'w1', '2026-04-23 11:40:00', '0', '', ''], # Row 7: claimed stale, eligible
        ]
        from config import LOCAL_TZ
        from datetime import datetime
        now_dt = datetime(2026, 4, 23, 11, 55, 0, tzinfo=LOCAL_TZ)
        with patch('database.reminders.get_worksheet', return_value=mock_sheet):
            due = get_due_reminders(now_dt=now_dt, max_retries=3, lock_duration_minutes=10)
        
        self.assertEqual(len(due), 2)
        row_nums = {item['row_num'] for item in due}
        self.assertEqual(row_nums, {2, 7})
        self.assertEqual(due[0]['user_id'], 'u1')
        self.assertEqual(due[1]['user_id'], 'u6')

    def test_claim_reminder_success(self):
        from database.reminders import claim_reminder
        mock_sheet = MagicMock()
        # Mock row values for initial read and read-back verification
        mock_sheet.row_values.side_effect = [
            ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'scheduled', ''], # row_values (read)
            ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'claimed', '', 'worker_test', '2026-04-23 11:55:00', '0', '', '2026-04-23 11:55:00'] # row_values (read-back)
        ]
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes', 'Claimed_By', 'Claimed_At', 'Retry_Count', 'Last_Error', 'Last_Attempt_At']
        ]
        from config import LOCAL_TZ
        from datetime import datetime
        now_dt = datetime(2026, 4, 23, 11, 55, 0, tzinfo=LOCAL_TZ)
        with patch('database.reminders.get_worksheet', return_value=mock_sheet):
            claimed = claim_reminder(2, 'u1', 'day3', 'worker_test', lock_duration_minutes=10, now_dt=now_dt)
        
        self.assertTrue(claimed)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        update_map = {u['range']: u['values'][0][0] for u in updates}
        self.assertEqual(update_map['F2'], 'claimed')
        self.assertEqual(update_map['H2'], 'worker_test')

    def test_claim_reminder_concurrency_conflict(self):
        from database.reminders import claim_reminder
        mock_sheet = MagicMock()
        mock_sheet.row_values.side_effect = [
            ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'scheduled', ''], # row_values (read)
            ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'claimed', '', 'worker_other', '2026-04-23 11:55:00', '0', '', '2026-04-23 11:55:00'] # row_values (read-back shows won by other)
        ]
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes', 'Claimed_By', 'Claimed_At', 'Retry_Count', 'Last_Error', 'Last_Attempt_At']
        ]
        from config import LOCAL_TZ
        from datetime import datetime
        now_dt = datetime(2026, 4, 23, 11, 55, 0, tzinfo=LOCAL_TZ)
        with patch('database.reminders.get_worksheet', return_value=mock_sheet):
            claimed = claim_reminder(2, 'u1', 'day3', 'worker_test', lock_duration_minutes=10, now_dt=now_dt)
        
        self.assertFalse(claimed)

    def test_handle_reminder_send_success(self):
        from database.reminders import handle_reminder_send_success
        mock_sheet_schedules = MagicMock()
        mock_sheet_schedules.row_values.return_value = ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'claimed', '', 'worker_test', '2026-04-23 11:55:00', '0', '', '']
        mock_sheet_schedules.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes', 'Claimed_By', 'Claimed_At', 'Retry_Count', 'Last_Error', 'Last_Attempt_At']
        ]
        mock_sheet_logs = MagicMock()
        
        def get_mock_worksheet(name):
            if name == 'ReminderSchedules':
                return mock_sheet_schedules
            return mock_sheet_logs

        with patch('database.reminders.get_worksheet', side_effect=get_mock_worksheet):
            success = handle_reminder_send_success(2, 'u1', 'day3', message_text="Reminder message content")
            
        self.assertTrue(success)
        mock_sheet_schedules.batch_update.assert_called_once()
        mock_sheet_logs.append_row.assert_called_once()
        log_row = mock_sheet_logs.append_row.call_args[0][0]
        self.assertEqual(log_row[1], 'u1')
        self.assertEqual(log_row[2], 'day3')
        self.assertEqual(log_row[3], 'sent')
        self.assertEqual(log_row[5], 'Reminder message content')

    def test_handle_reminder_send_failure_under_limit(self):
        from database.reminders import handle_reminder_send_failure
        mock_sheet = MagicMock()
        mock_sheet.row_values.return_value = ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'claimed', '', 'worker_test', '2026-04-23 11:55:00', '1', '', '']
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes', 'Claimed_By', 'Claimed_At', 'Retry_Count', 'Last_Error', 'Last_Attempt_At']
        ]
        with patch('database.reminders.get_worksheet', return_value=mock_sheet):
            success = handle_reminder_send_failure(2, 'u1', 'day3', error_message="LINE API error", max_retries=3, backoff_minutes=15)
            
        self.assertTrue(success)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        update_map = {u['range']: u['values'][0][0] for u in updates}
        self.assertEqual(update_map['F2'], 'failed')
        self.assertEqual(update_map['J2'], 2)
        self.assertEqual(update_map['K2'], 'LINE API error')
        self.assertEqual(update_map['H2'], '')

    def test_handle_reminder_send_failure_dead_letter(self):
        from database.reminders import handle_reminder_send_failure
        mock_sheet = MagicMock()
        mock_sheet.row_values.return_value = ['2026-04-20 09:00:00', 'u1', '2026-04-17', 'day3', '2026-04-20 09:00:00', 'claimed', '', 'worker_test', '2026-04-23 11:55:00', '2', '', '']
        mock_sheet.get_all_values.return_value = [
            ['Created_At', 'User_ID', 'Discharge_Date', 'Reminder_Type', 'Scheduled_Date', 'Status', 'Notes', 'Claimed_By', 'Claimed_At', 'Retry_Count', 'Last_Error', 'Last_Attempt_At']
        ]
        with patch('database.reminders.get_worksheet', return_value=mock_sheet):
            success = handle_reminder_send_failure(2, 'u1', 'day3', error_message="LINE API error permanent", max_retries=3, backoff_minutes=15)
            
        self.assertTrue(success)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        update_map = {u['range']: u['values'][0][0] for u in updates}
        self.assertEqual(update_map['F2'], 'dead_letter')
        self.assertEqual(update_map['J2'], 3)
        self.assertEqual(update_map['K2'], 'LINE API error permanent')
        self.assertEqual(update_map['H2'], '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
