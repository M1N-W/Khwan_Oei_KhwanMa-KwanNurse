# -*- coding: utf-8 -*-
"""
Tests for KWN-08: Survey Completion and Dashboard Analytics
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
os.environ["NURSE_DASHBOARD_AUTH"] = "nurse_kwan:$2b$04$v9.jXQfJtH3r9p7Z6QnQe.Nlh5Cg8Nn3H9gK0Z0uX7nN2p3/v4q7y"
os.environ["FLASK_SECRET_KEY"] = "test-secret-key-bell"

from app import create_app
from config import LOCAL_TZ
import database.surveys as db_surveys
import services.dashboard_readers as readers


class TestSurveyAnalytics(unittest.TestCase):
    """Test suite for survey database analytics and readers."""

    def test_get_survey_summary_for_user(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"],
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "sent", "2026-06-20 09:00:00", "", "", "", "0", "", "2026-06-20 09:00:00"], # Row 2: U-1, sent, >72 hours (overdue)
            ["2026-06-23 09:00:00", "U-1", "14", "url2", "tok2", "clicked", "2026-06-23 09:00:00", "2026-06-23 09:05:00", "", "", "0", "", "2026-06-23 09:00:00"], # Row 3: U-1, clicked
            ["2026-06-23 09:00:00", "U-1", "21", "url3", "tok3", "scheduled", "", "", "", "", "0", "", "2026-06-24 09:00:00"], # Row 4: U-1, scheduled (not sent yet)
            ["2026-06-23 09:00:00", "U-2", "7", "url4", "tok4", "sent", "2026-06-23 09:00:00", "", "", "", "0", "", "2026-06-23 09:00:00"], # Row 5: U-2, sent, recent (not overdue)
        ]
        
        now = datetime(2026, 6, 23, 10, 0, 0, tzinfo=LOCAL_TZ) # current time
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            summary = db_surveys.get_survey_summary_for_user("U-1", now_dt=now)
            
        self.assertEqual(summary["sent"], 2) # Row 2 and 3
        self.assertEqual(summary["clicked"], 1) # Row 3
        self.assertEqual(summary["overdue"], 1) # Row 2 is > 72 hours ago

    def test_get_patient_survey_timeline(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"],
            ["2026-06-23 09:00:00", "U-1", "14", "url2", "tok2", "clicked", "2026-06-23 09:00:00", "2026-06-23 09:05:00", "", "", "0", "", "2026-06-23 09:00:00"],
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "sent", "2026-06-20 09:00:00", "", "", "", "0", "", "2026-06-20 09:00:00"],
        ]
        
        now = datetime(2026, 6, 23, 10, 0, 0, tzinfo=LOCAL_TZ)
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            timeline = db_surveys.get_patient_survey_timeline("U-1", now_dt=now)
            
        self.assertEqual(len(timeline), 2)
        # Should be sorted by milestone_day (7 then 14)
        self.assertEqual(timeline[0]["milestone_day"], 7)
        self.assertTrue(timeline[0]["is_overdue"])
        self.assertEqual(timeline[1]["milestone_day"], 14)
        self.assertFalse(timeline[1]["is_overdue"])

    def test_get_survey_analytics(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Scheduled_Date"],
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "sent", "2026-06-20 09:00:00", "", "", "", "0", "", ""],
            ["2026-06-23 09:00:00", "U-2", "7", "url2", "tok2", "clicked", "2026-06-20 09:00:00", "2026-06-20 09:05:00", "", "", "0", "", ""],
            ["2026-06-23 09:00:00", "U-3", "7", "url3", "tok3", "failed", "", "", "", "", "3", "Error", ""],
            ["2026-06-23 09:00:00", "U-4", "7", "url4", "tok4", "scheduled", "", "", "", "", "0", "", ""],
        ]
        
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            stats = db_surveys.get_survey_analytics()
            
        self.assertEqual(stats["sent"], 2) # sent and clicked
        self.assertEqual(stats["clicked"], 1) # clicked
        self.assertEqual(stats["ctr"], 50.0) # 1 / 2 * 100
        self.assertEqual(stats["failed"], 1) # failed
        self.assertEqual(stats["scheduled"], 1) # scheduled

    def test_get_survey_analytics_zero_sent(self):
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status"],
            ["2026-06-23 09:00:00", "U-1", "7", "url1", "tok1", "scheduled"],
        ]
        
        with patch("database.surveys.get_worksheet", return_value=mock_sheet):
            stats = db_surveys.get_survey_analytics()
            
        self.assertEqual(stats["sent"], 0)
        self.assertEqual(stats["clicked"], 0)
        self.assertEqual(stats["ctr"], 0.0) # Division by zero safety check

    @patch("database.surveys.get_survey_analytics")
    def test_analytics_reader_caching(self, mock_get_analytics):
        mock_get_analytics.return_value = {"sent": 10, "clicked": 5, "ctr": 50.0, "failed": 0, "scheduled": 2}
        
        # First call should hit database function
        res1 = readers.get_survey_analytics_reader(force_refresh=True)
        self.assertEqual(res1["sent"], 10)
        mock_get_analytics.assert_called_once()
        
        # Second call should hit cache, so count shouldn't increase
        mock_get_analytics.reset_mock()
        res2 = readers.get_survey_analytics_reader(force_refresh=False)
        self.assertEqual(res2["sent"], 10)
        mock_get_analytics.assert_not_called()

        # Force refresh should hit database again
        res3 = readers.get_survey_analytics_reader(force_refresh=True)
        self.assertEqual(res3["sent"], 10)
        mock_get_analytics.assert_called_once()


class TestDashboardSurveyIntegration(unittest.TestCase):
    """Test dashboard routes render with survey analytics data."""

    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    @patch("services.auth.is_dashboard_enabled")
    @patch("services.auth.current_nurse")
    @patch("routes.dashboard.views.get_csrf_token")
    @patch("routes.dashboard.views.get_home_stats")
    @patch("routes.dashboard.views.get_queue_snapshot")
    @patch("routes.dashboard.views.get_recent_alerts")
    @patch("routes.dashboard.views.get_survey_analytics_reader")
    def test_dashboard_home_renders_survey_analytics(
        self, mock_survey, mock_alerts, mock_queue, mock_stats, mock_csrf, mock_nurse, mock_enabled
    ):
        mock_enabled.return_value = True
        mock_nurse.return_value = "nurse_kwan"
        mock_csrf.return_value = "csrf123"
        mock_stats.return_value = {
            "queue_total": 0,
            "queue_high_priority": 0,
            "alerts_today": 0,
            "alerts_7d": 0,
            "failed_alerts_actionable": 0,
            "failed_alerts_degraded": False,
            "refreshed_at": "10:00"
        }
        mock_queue.return_value = []
        mock_alerts.return_value = []
        mock_survey.return_value = {
            "sent": 15,
            "clicked": 9,
            "ctr": 60.0,
            "failed": 1,
            "scheduled": 5,
        }

        response = self.client.get("/dashboard/")
                
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("สถิติการตอบแบบสอบถาม (Survey Analytics)", html)
        self.assertIn("60.0%", html)
        self.assertIn("15", html) # Sent count
        self.assertIn("9", html)  # Clicked count

    @patch("services.auth.is_dashboard_enabled")
    @patch("services.auth.current_nurse")
    @patch("routes.dashboard.views.get_csrf_token")
    @patch("routes.dashboard.views.get_patient_timeline")
    @patch("routes.dashboard.views.get_patient_trend")
    @patch("routes.dashboard.views.get_patient_survey_timeline_reader")
    def test_dashboard_patient_renders_survey_timeline(
        self, mock_survey_timeline, mock_trend, mock_timeline, mock_csrf, mock_nurse, mock_enabled
    ):
        mock_enabled.return_value = True
        mock_nurse.return_value = "nurse_kwan"
        mock_csrf.return_value = "csrf123"
        mock_timeline.return_value = {
            "user_id": "U-1",
            "user_id_short": "U-1",
            "patient_display_name": "สมชาย ใจดี",
            "patient_hn": "HN12345",
            "patient_phone_masked": "08X-XXX-5678",
            "patient_registration_status": "registered",
            "events": []
        }
        mock_trend.return_value = None
        mock_survey_timeline.return_value = [
            {
                "milestone_day": 7,
                "scheduled_date": "2026-06-25",
                "status": "clicked",
                "sent_at": "2026-06-25 09:00:00",
                "clicked_at": "2026-06-25 09:10:00",
                "is_overdue": False
            },
            {
                "milestone_day": 14,
                "scheduled_date": "2026-07-02",
                "status": "sent",
                "sent_at": "2026-07-02 09:00:00",
                "clicked_at": "",
                "is_overdue": True
            }
        ]

        response = self.client.get("/dashboard/patient/U-1")
                
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("สถานะแบบสอบถาม Milestone (Milestone Surveys)", html)
        self.assertIn("Milestone Day 7", html)
        self.assertIn("คลิกเปิดลิงก์แล้ว", html)
        self.assertIn("Milestone Day 14", html)
        self.assertIn("ส่งลิงก์แล้ว", html)
        self.assertIn("ยังไม่ตอบกลับ (เกิน 72 ชม.)", html)


if __name__ == "__main__":
    unittest.main()
