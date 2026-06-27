# -*- coding: utf-8 -*-
"""
Tests for KWN-07: Survey Scheduling and Tracking
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Setup environment variables
os.environ.setdefault("NURSE_GROUP_ID", "test_nurse_group")

from app import create_app
from config import LOCAL_TZ
import database.surveys as db_surveys
import services.survey as svc_survey


class TestSurveyDatabaseOperations(unittest.TestCase):
    """Test database helper operations in database/surveys.py."""

    def test_save_survey_schedule(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"]
        ]
        
        now = datetime(2026, 6, 23, 9, 0, 0, tzinfo=LOCAL_TZ)
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            ok = db_surveys.save_survey_schedule(
                user_id="U-1",
                milestone_day=7,
                survey_url="https://google.com/form",
                tracking_token="token123",
                scheduled_at=now
            )
            
        self.assertTrue(ok)
        mock_sheet.append_row.assert_called_once()
        row = mock_sheet.append_row.call_args[0][0]
        self.assertEqual(row[1], "U-1")
        self.assertEqual(row[2], "7")
        self.assertEqual(row[3], "https://google.com/form")
        self.assertEqual(row[4], "token123")
        self.assertEqual(row[5], "scheduled")
        self.assertEqual(row[12], "2026-06-23 09:00:00")

    def test_has_scheduled_surveys(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID"],
            ["2026-06-23 09:00:00", "U-1"]
        ]
        
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            has_surveys_exist = db_surveys.has_scheduled_surveys("U-1")
            has_surveys_none = db_surveys.has_scheduled_surveys("U-2")
            
        self.assertTrue(has_surveys_exist)
        self.assertFalse(has_surveys_none)

    def test_get_due_surveys(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"],
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "scheduled", "", "", "", "", "0", "", "2026-06-23 09:00:00"], # Row 2: due
            ["2026-06-23 09:00:00", "U-2", "14", "url2", "tok2", "scheduled", "", "", "", "", "0", "", "2026-06-23 11:00:00"], # Row 3: not due
            ["2026-06-23 09:00:00", "U-3", "21", "url3", "tok3", "sent", "", "", "", "", "0", "", "2026-06-23 09:00:00"], # Row 4: sent, not eligible
            ["2026-06-23 09:00:00", "U-4", "30", "url4", "tok4", "claimed", "", "", "workerA", "2026-06-23 10:00:00", "0", "", "2026-06-23 09:00:00"], # Row 5: claimed (active lease)
            ["2026-06-23 09:00:00", "U-5", "7", "url5", "tok5", "claimed", "", "", "workerA", "2026-06-23 09:40:00", "0", "", "2026-06-23 09:00:00"], # Row 6: claimed (expired lease, due)
        ]
        
        # Test current time is 2026-06-23 10:00:00 (Row 6 lease expired after 10 mins)
        now = datetime(2026, 6, 23, 10, 0, 0, tzinfo=LOCAL_TZ)
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            due = db_surveys.get_due_surveys(now_dt=now, lock_duration_minutes=10)
            
        self.assertEqual(len(due), 2)
        row_nums = [d["row_num"] for d in due]
        self.assertEqual(row_nums, [2, 6])
        self.assertEqual(due[0]["user_id"], "U-1")
        self.assertEqual(due[1]["user_id"], "U-5")

    def test_claim_survey_success(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"]
        ]
        mock_sheet.row_values.side_effect = [
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "scheduled", "", "", "", "", "0", "", "2026-06-23 09:00:00"], # read
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "claimed", "", "", "worker1", "2026-06-23 10:00:00", "0", "", "2026-06-23 09:00:00"] # verify read-back
        ]
        
        now = datetime(2026, 6, 23, 10, 0, 0, tzinfo=LOCAL_TZ)
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            ok = db_surveys.claim_survey(2, "U-1", "worker1", now_dt=now)
            
        self.assertTrue(ok)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        update_map = {u["range"]: u["values"][0][0] for u in updates}
        self.assertEqual(update_map["F2"], "claimed")
        self.assertEqual(update_map["I2"], "worker1")

    def test_claim_survey_concurrency_conflict(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"]
        ]
        # read-back shows claimed by another worker
        mock_sheet.row_values.side_effect = [
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "scheduled", "", "", "", "", "0", "", "2026-06-23 09:00:00"],
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "claimed", "", "", "worker2", "2026-06-23 10:00:00", "0", "", "2026-06-23 09:00:00"]
        ]
        
        now = datetime(2026, 6, 23, 10, 0, 0, tzinfo=LOCAL_TZ)
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            ok = db_surveys.claim_survey(2, "U-1", "worker1", now_dt=now)
            
        self.assertFalse(ok)

    def test_mark_survey_clicked(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"],
            ["2026-06-23 09:00:00", "U-1", "7", "https://google.com/form?id=123", "token123", "sent", "", "", "", "", "0", "", "2026-06-23 09:00:00"]
        ]
        
        now = datetime(2026, 6, 23, 10, 0, 0, tzinfo=LOCAL_TZ)
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            url = db_surveys.mark_survey_clicked("token123", now_dt=now)
            
        self.assertEqual(url, "https://google.com/form?id=123")
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        # Clicked_At is column H (8th) -> idx 7 -> H2
        # Status is column F (6th) -> idx 5 -> F2
        update_map = {u["range"]: u["values"][0][0] for u in updates}
        self.assertEqual(update_map["H2"], "2026-06-23 10:00:00")
        self.assertEqual(update_map["F2"], "clicked")


class TestSurveyService(unittest.TestCase):
    """Test survey workflow coordination in services/survey.py."""

    @patch("services.survey.has_scheduled_surveys", return_value=False)
    @patch("services.survey.save_survey_schedule", return_value=True)
    def test_schedule_milestone_surveys_new_patient(self, mock_save, mock_has):
        now = datetime(2026, 6, 23, 8, 30, 0, tzinfo=LOCAL_TZ)
        ok = svc_survey.schedule_milestone_surveys("U-1", now)
        self.assertTrue(ok)
        self.assertEqual(mock_save.call_count, 4)
        
        # Verify first call scheduled at 9 AM of activation_date + 7 days
        args = mock_save.call_args_list[0][1]
        self.assertEqual(args["user_id"], "U-1")
        self.assertEqual(args["milestone_day"], 7)
        self.assertEqual(args["scheduled_at"], datetime(2026, 6, 30, 9, 0, 0, tzinfo=LOCAL_TZ))

    @patch("services.survey.has_scheduled_surveys", return_value=True)
    @patch("services.survey.save_survey_schedule")
    def test_schedule_milestone_surveys_duplicate(self, mock_save, mock_has):
        ok = svc_survey.schedule_milestone_surveys("U-1")
        self.assertFalse(ok)
        mock_save.assert_not_called()

    @patch("services.survey.get_due_surveys")
    @patch("services.survey.claim_survey")
    @patch("services.survey.push_rich_message")
    @patch("services.survey.mark_survey_sent")
    def test_process_due_surveys_success(self, mock_sent, mock_push, mock_claim, mock_get):
        mock_get.return_value = [
            {"row_num": 2, "user_id": "U-1", "milestone_day": 7, "survey_url": "url1", "tracking_token": "tok1", "status": "scheduled", "retry_count": 0}
        ]
        mock_claim.return_value = True
        mock_push.return_value = True
        
        count = svc_survey.process_due_surveys()
        self.assertEqual(count, 1)
        mock_claim.assert_called_once()
        mock_push.assert_called_once()
        mock_sent.assert_called_once_with(2, "U-1", now_dt=unittest.mock.ANY)


class TestSurveyRouteIntegration(unittest.TestCase):
    """Test webhook track redirect integration."""

    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    @patch("database.surveys.mark_survey_clicked", return_value="https://google.com/form?abc")
    def test_track_redirect_valid_token(self, mock_clicked):
        resp = self.client.get("/track/token123")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://google.com/form?abc")
        mock_clicked.assert_called_once_with("token123")

    @patch("database.surveys.mark_survey_clicked", return_value=None)
    def test_track_redirect_invalid_token_fallback(self, mock_clicked):
        resp = self.client.get("/track/invalid")
        self.assertEqual(resp.status_code, 302)
        # Should redirect to default form
        self.assertIn("docs.google.com/forms", resp.headers["Location"])


if __name__ == "__main__":
    unittest.main()
