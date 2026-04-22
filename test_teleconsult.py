# -*- coding: utf-8 -*-
"""
Regression tests for teleconsult and related phase-1 stability fixes.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Keep imports repo-local and disable scheduler side effects during test import.
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("RUN_SCHEDULER", "false")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

from app import should_run_scheduler
from database.teleconsult import get_user_active_session, update_session_status
from services.teleconsult import (
    cancel_consultation,
    get_category_menu,
    get_queue_info_message,
    parse_category_choice,
    start_teleconsult,
)


class SchedulerOwnershipTests(unittest.TestCase):
    def test_should_run_scheduler_respects_env_flag(self):
        with patch.dict(os.environ, {"RUN_SCHEDULER": "false"}, clear=False):
            self.assertFalse(should_run_scheduler())

        with patch.dict(os.environ, {"RUN_SCHEDULER": "true"}, clear=False):
            self.assertTrue(should_run_scheduler())


class TeleconsultDatabaseTests(unittest.TestCase):
    def test_get_user_active_session_includes_after_hours_pending(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Session_ID', 'Timestamp', 'User_ID', 'Issue_Type', 'Priority', 'Status', 'Description'],
            ['S1', '2026-04-22 09:00:00', 'u1', 'wound', '3', 'after_hours_pending', 'desc'],
        ]

        with patch('database.teleconsult.get_worksheet', return_value=mock_sheet):
            result = get_user_active_session('u1')

        self.assertIsNotNone(result)
        self.assertEqual(result['Status'], 'after_hours_pending')

    def test_update_session_status_uses_batch_update_for_multi_field_write(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ['Session_ID', 'Timestamp', 'User_ID', 'Issue_Type', 'Priority', 'Status', 'Description', 'Queue_Position', 'Assigned_Nurse', 'Started_At', 'Completed_At', 'Notes'],
            ['S9', '2026-04-22 09:00:00', 'u9', 'wound', '2', 'queued', 'desc', '', '', '', '', ''],
        ]

        with patch('database.teleconsult.get_worksheet', return_value=mock_sheet):
            success = update_session_status('S9', 'in_progress', assigned_nurse='nurse-1', notes='picked up')

        self.assertTrue(success)
        self.assertTrue(mock_sheet.batch_update.called)
        updates = mock_sheet.batch_update.call_args[0][0]
        update_ranges = {item['range'] for item in updates}
        self.assertIn('F2', update_ranges)
        self.assertIn('I2', update_ranges)
        self.assertIn('J2', update_ranges)
        self.assertIn('L2', update_ranges)


class TeleconsultServiceTests(unittest.TestCase):
    def test_category_menu_contains_all_options(self):
        menu = get_category_menu()
        self.assertIn('ฉุกเฉิน', menu)
        self.assertIn('ถามเรื่องยา', menu)
        self.assertIn('แผลผ่าตัด', menu)
        self.assertIn('นัดหมาย/เอกสาร', menu)
        self.assertIn('อื่นๆ', menu)

    def test_category_parsing_supports_number_and_text(self):
        self.assertEqual(parse_category_choice('1'), 'emergency')
        self.assertEqual(parse_category_choice('2'), 'medication')
        self.assertEqual(parse_category_choice('ฉุกเฉิน'), 'emergency')
        self.assertEqual(parse_category_choice('ถามเรื่องยา'), 'medication')
        self.assertEqual(parse_category_choice('wound'), 'wound')
        self.assertIsNone(parse_category_choice('invalid'))

    def test_start_teleconsult_rejects_existing_active_session(self):
        existing = {'Queue_Position': '2', 'Issue_Type': 'wound'}
        with patch('services.teleconsult.get_user_active_session', return_value=existing):
            result = start_teleconsult('user-1', 'wound', 'desc')

        self.assertFalse(result['success'])
        self.assertIn('กำลังดำเนินการอยู่แล้ว', result['message'])
        self.assertIn('ตำแหน่งในคิว: 2', result['message'])

    def test_start_teleconsult_routes_after_hours(self):
        with patch('services.teleconsult.get_user_active_session', return_value=None), \
             patch('services.teleconsult.is_office_hours', return_value=False), \
             patch('services.teleconsult.handle_after_hours', return_value={'success': True, 'message': 'after-hours'}) as mock_after:
            result = start_teleconsult('user-2', 'wound', 'desc')

        self.assertEqual(result['message'], 'after-hours')
        mock_after.assert_called_once_with('user-2', 'wound', 'desc')

    def test_start_teleconsult_returns_queue_full_message(self):
        with patch('services.teleconsult.get_user_active_session', return_value=None), \
             patch('services.teleconsult.is_office_hours', return_value=True), \
             patch('services.teleconsult.get_queue_status', return_value={'total': 20}):
            result = start_teleconsult('user-3', 'wound', 'desc')

        self.assertFalse(result['success'])
        self.assertIn('คิวเต็มแล้ว', result['message'])

    def test_start_teleconsult_rolls_back_when_queue_insert_fails(self):
        session = {
            'session_id': 'S2',
            'user_id': 'user-4',
            'issue_type': 'wound',
            'priority': 2,
            'description': 'desc',
        }
        with patch('services.teleconsult.get_user_active_session', return_value=None), \
             patch('services.teleconsult.is_office_hours', return_value=True), \
             patch('services.teleconsult.get_queue_status', return_value={'total': 0}), \
             patch('services.teleconsult.create_session', return_value=session), \
             patch('services.teleconsult.add_to_queue', return_value=None), \
             patch('services.teleconsult.update_session_status') as mock_update:
            result = start_teleconsult('user-4', 'wound', 'desc')

        self.assertFalse(result['success'])
        self.assertIn('เกิดข้อผิดพลาดในการเข้าคิว', result['message'])
        mock_update.assert_called_once_with('S2', 'queue_failed', notes='Queue insertion failed')

    def test_start_teleconsult_success_path_returns_queue_info(self):
        session = {
            'session_id': 'S3',
            'user_id': 'user-5',
            'issue_type': 'medication',
            'priority': 2,
            'description': 'drug question',
        }
        queue_info = {
            'queue_id': 'Q1',
            'session_id': 'S3',
            'position': 1,
            'estimated_wait': 15,
            'timestamp': '2026-04-22 10:00:00',
        }
        with patch('services.teleconsult.get_user_active_session', return_value=None), \
             patch('services.teleconsult.is_office_hours', return_value=True), \
             patch('services.teleconsult.get_queue_status', return_value={'total': 0}), \
             patch('services.teleconsult.create_session', return_value=session), \
             patch('services.teleconsult.add_to_queue', return_value=queue_info), \
             patch('services.teleconsult.alert_nurse_new_request') as mock_alert:
            result = start_teleconsult('user-5', 'medication', 'drug question')

        self.assertTrue(result['success'])
        self.assertEqual(result['session'], session)
        self.assertEqual(result['queue'], queue_info)
        self.assertIn('ตำแหน่งในคิว: 1', result['message'])
        mock_alert.assert_called_once_with(session, queue_info)

    def test_cancel_consultation_handles_missing_session(self):
        with patch('services.teleconsult.get_user_active_session', return_value=None):
            result = cancel_consultation('user-6')

        self.assertFalse(result['success'])
        self.assertIn('ไม่พบคำขอปรึกษา', result['message'])

    def test_cancel_consultation_updates_session_and_queue(self):
        session = {'Session_ID': 'S4'}
        with patch('services.teleconsult.get_user_active_session', return_value=session), \
             patch('services.teleconsult.update_session_status', return_value=True) as mock_update, \
             patch('services.teleconsult.remove_from_queue', return_value=True) as mock_remove:
            result = cancel_consultation('user-7')

        self.assertTrue(result['success'])
        self.assertIn('ยกเลิกคำขอแล้ว', result['message'])
        mock_update.assert_called_once_with('S4', 'cancelled', notes='Cancelled by user')
        mock_remove.assert_called_once_with('S4')

    def test_get_queue_info_message_formats_empty_queue(self):
        with patch('services.teleconsult.get_queue_status', return_value={'total': 0, 'by_priority': {}}):
            message = get_queue_info_message()
        self.assertEqual(message, '📊 ขณะนี้ไม่มีคิวรอค่ะ')

    def test_get_queue_info_message_formats_priority_counts(self):
        with patch('services.teleconsult.get_queue_status', return_value={'total': 4, 'by_priority': {1: 1, 2: 2, 3: 1}}):
            message = get_queue_info_message()
        self.assertIn('รวมทั้งหมด: 4 คน', message)
        self.assertIn('ฉุกเฉิน: 1 คน', message)
        self.assertIn('กลาง: 2 คน', message)
        self.assertIn('ต่ำ: 1 คน', message)


if __name__ == '__main__':
    unittest.main(verbosity=2)
