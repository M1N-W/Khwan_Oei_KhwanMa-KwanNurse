# -*- coding: utf-8 -*-
"""
Tests for Quick-win D3-A — Patient timeline + EducationLog persistence.

Coverage:
1. EducationLog: save_education_view + get_recent_education round-trip
2. Auto-create on first write when sheet missing
3. handle_get_knowledge logs view (best-effort, never raises)
4. handle_recommend_knowledge logs each recommendation
5. get_patient_timeline merges 4 event types correctly
6. patient.html renders all 4 event types via Flask test client
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from config import LOCAL_TZ


def _app_context():
    """Build a minimal Flask app for tests that need jsonify()."""
    from flask import Flask
    return Flask(__name__).app_context()


# -----------------------------------------------------------------------------
# 1. EducationLog persistence round-trip
# -----------------------------------------------------------------------------
class EducationLogPersistenceTests(unittest.TestCase):

    def test_save_returns_false_for_empty_user_id(self):
        from database.education_logs import save_education_view
        self.assertFalse(save_education_view("", "wound_care", "GetKnowledge"))

    def test_save_appends_row_with_canonical_shape(self):
        from database import education_logs

        fake_sheet = MagicMock()
        with patch.object(education_logs, "_get_or_create_sheet", return_value=fake_sheet):
            ok = education_logs.save_education_view(
                user_id="U-test-1",
                topic="wound_care",
                source="GetKnowledge",
                personalized=False,
            )
        self.assertTrue(ok)
        fake_sheet.append_row.assert_called_once()
        row = fake_sheet.append_row.call_args.args[0]
        # [Timestamp, User_ID, Topic, Source, Personalized]
        self.assertEqual(len(row), 5)
        self.assertEqual(row[1], "U-test-1")
        self.assertEqual(row[2], "wound_care")
        self.assertEqual(row[3], "GetKnowledge")
        self.assertEqual(row[4], "false")

    def test_save_serializes_personalized_true_as_string(self):
        from database import education_logs

        fake_sheet = MagicMock()
        with patch.object(education_logs, "_get_or_create_sheet", return_value=fake_sheet):
            education_logs.save_education_view(
                "U-test-2", "medication", "RecommendKnowledge", personalized=True,
            )
        row = fake_sheet.append_row.call_args.args[0]
        self.assertEqual(row[4], "true")

    def test_save_swallows_exceptions(self):
        """Audit must never break user replies."""
        from database import education_logs

        fake_sheet = MagicMock()
        fake_sheet.append_row.side_effect = RuntimeError("boom")
        with patch.object(education_logs, "_get_or_create_sheet", return_value=fake_sheet):
            ok = education_logs.save_education_view(
                "U-test-3", "wound_care", "GetKnowledge",
            )
        self.assertFalse(ok)  # returns False, but doesn't raise

    def test_save_truncates_long_topic(self):
        from database import education_logs

        fake_sheet = MagicMock()
        long_topic = "x" * 200
        with patch.object(education_logs, "_get_or_create_sheet", return_value=fake_sheet):
            education_logs.save_education_view(
                "U-test-4", long_topic, "GetKnowledge",
            )
        row = fake_sheet.append_row.call_args.args[0]
        self.assertEqual(len(row[2]), 100)


# -----------------------------------------------------------------------------
# 2. Auto-create sheet on first write
# -----------------------------------------------------------------------------
class EducationLogAutoCreateTests(unittest.TestCase):

    def test_get_or_create_returns_existing_sheet(self):
        from database import education_logs

        existing = MagicMock()
        with patch.object(education_logs, "get_worksheet", return_value=existing):
            result = education_logs._get_or_create_sheet()
        self.assertIs(result, existing)

    def test_get_or_create_creates_new_sheet_with_header(self):
        from database import education_logs

        fake_spread = MagicMock()
        new_sheet = MagicMock()
        fake_spread.add_worksheet.return_value = new_sheet

        with patch.object(education_logs, "get_worksheet", return_value=None), \
             patch.object(education_logs, "get_spreadsheet", return_value=fake_spread):
            result = education_logs._get_or_create_sheet()

        self.assertIs(result, new_sheet)
        fake_spread.add_worksheet.assert_called_once()
        # Header row appended
        new_sheet.append_row.assert_called_once()
        header = new_sheet.append_row.call_args.args[0]
        self.assertEqual(
            header,
            ["Timestamp", "User_ID", "Topic", "Source", "Personalized"],
        )

    def test_get_or_create_returns_none_when_no_spreadsheet(self):
        from database import education_logs
        with patch.object(education_logs, "get_worksheet", return_value=None), \
             patch.object(education_logs, "get_spreadsheet", return_value=None):
            self.assertIsNone(education_logs._get_or_create_sheet())


# -----------------------------------------------------------------------------
# 3. get_recent_education round-trip
# -----------------------------------------------------------------------------
class GetRecentEducationTests(unittest.TestCase):

    def test_returns_empty_when_sheet_missing(self):
        from database import education_logs
        with patch.object(education_logs, "get_worksheet", return_value=None):
            self.assertEqual(education_logs.get_recent_education("U-x"), [])

    def test_filters_by_user_and_window(self):
        from database import education_logs

        now = datetime.now(tz=LOCAL_TZ)
        fmt = "%Y-%m-%d %H:%M:%S"
        rows = [
            ["Timestamp", "User_ID", "Topic", "Source", "Personalized"],
            [(now - timedelta(days=1)).strftime(fmt), "U-a", "wound_care", "GetKnowledge", "false"],
            [(now - timedelta(days=5)).strftime(fmt), "U-b", "medication", "GetKnowledge", "true"],
            [(now - timedelta(days=40)).strftime(fmt), "U-a", "old_topic", "GetKnowledge", "false"],
            [(now - timedelta(hours=1)).strftime(fmt), "U-a", "physical_therapy", "RecommendKnowledge", "true"],
        ]
        fake_sheet = MagicMock()
        fake_sheet.get_all_values.return_value = rows

        with patch.object(education_logs, "get_worksheet", return_value=fake_sheet):
            out = education_logs.get_recent_education(user_id="U-a", days=30)

        topics = [r["topic"] for r in out]
        self.assertIn("physical_therapy", topics)
        self.assertIn("wound_care", topics)
        self.assertNotIn("old_topic", topics)  # outside 30-day window
        self.assertNotIn("medication", topics)  # different user
        # Newest first
        self.assertEqual(topics[0], "physical_therapy")
        # personalized parsed correctly
        self.assertTrue(out[0]["personalized"])
        self.assertFalse(out[1]["personalized"])


# -----------------------------------------------------------------------------
# 4. handle_get_knowledge logs view
# -----------------------------------------------------------------------------
class GetKnowledgeAuditTests(unittest.TestCase):

    def test_logs_canonical_key_for_thai_query(self):
        from routes import webhook

        with _app_context(), \
             patch.object(webhook, "save_education_view") as mock_save, \
             patch.object(webhook, "get_wound_care_guide", return_value="GUIDE"):
            webhook.handle_get_knowledge(
                user_id="U-1",
                params={"topic": "ดูแลแผล"},
                query_text="ดูแลแผล",
            )
        mock_save.assert_called_once()
        kwargs = mock_save.call_args.kwargs
        self.assertEqual(kwargs["topic"], "wound_care")
        self.assertEqual(kwargs["source"], "GetKnowledge")
        self.assertFalse(kwargs["personalized"])

    def test_swallows_save_exception(self):
        from routes import webhook

        with _app_context(), \
             patch.object(webhook, "save_education_view", side_effect=RuntimeError("x")), \
             patch.object(webhook, "get_wound_care_guide", return_value="GUIDE"):
            # Must not raise — user reply is what matters
            resp, status = webhook.handle_get_knowledge(
                user_id="U-1",
                params={"topic": "wound_care"},
                query_text="",
            )
        self.assertEqual(status, 200)

    def test_does_not_log_for_menu_request(self):
        from routes import webhook

        with _app_context(), \
             patch.object(webhook, "save_education_view") as mock_save:
            webhook.handle_get_knowledge(
                user_id="U-2", params={}, query_text="ความรู้",
            )
        mock_save.assert_not_called()

    def test_does_not_log_for_unrecognized_topic(self):
        from routes import webhook

        with _app_context(), \
             patch.object(webhook, "save_education_view") as mock_save:
            webhook.handle_get_knowledge(
                user_id="U-3", params={"topic": "ไม่มีหัวข้อนี้"}, query_text="",
            )
        mock_save.assert_not_called()


# -----------------------------------------------------------------------------
# 5. handle_recommend_knowledge logs each recommendation
# -----------------------------------------------------------------------------
class RecommendKnowledgeAuditTests(unittest.TestCase):

    def test_logs_each_recommendation_as_personalized(self):
        from routes import webhook

        recs = [
            {"key": "wound_care", "title": "การดูแลแผล"},
            {"key": "medication", "title": "การรับประทานยา"},
            {"key": "", "title": "skip-empty"},  # should skip
        ]

        with _app_context(), \
             patch("services.patient_profile.get_or_build_profile",
                   return_value={"source": "stored"}), \
             patch.object(webhook, "recommend_guides", return_value=recs), \
             patch.object(webhook, "format_recommendations_message",
                          return_value="MESSAGE"), \
             patch.object(webhook, "save_education_view") as mock_save:
            webhook.handle_recommend_knowledge(user_id="U-r1", params={})

        # 2 calls (skip the empty key)
        self.assertEqual(mock_save.call_count, 2)
        keys = [c.kwargs["topic"] for c in mock_save.call_args_list]
        self.assertEqual(keys, ["wound_care", "medication"])
        # All marked as personalized
        for c in mock_save.call_args_list:
            self.assertTrue(c.kwargs["personalized"])
            self.assertEqual(c.kwargs["source"], "RecommendKnowledge")


# -----------------------------------------------------------------------------
# 6. get_patient_timeline merges 4 event types
# -----------------------------------------------------------------------------
class TimelineMergeTests(unittest.TestCase):

    def test_merges_symptom_session_wound_education(self):
        from services import dashboard_readers

        now = datetime.now(tz=LOCAL_TZ)

        symptom = {
            "timestamp": now - timedelta(hours=4),
            "risk_level": "high", "risk_score": 9,
            "pain": "8", "wound": "บวม", "fever": "ไม่มี", "mobility": "เดินไม่ได้",
        }
        session = {
            "timestamp": now - timedelta(hours=3),
            "session_id": "TC1", "issue_type": "emergency",
            "status": "completed", "assigned_nurse": "nurse_kwan", "notes": "ok",
        }
        wound = {
            "timestamp": now - timedelta(hours=2),
            "severity": "medium",
            "observations": ["มีหนอง", "บวมแดง"],
            "advice": "เปลี่ยนผ้าทุกวัน",
            "confidence": 0.85,
        }
        edu = {
            "timestamp": now - timedelta(hours=1),
            "topic": "wound_care",
            "source": "RecommendKnowledge",
            "personalized": True,
        }

        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=[symptom]), \
             patch.object(dashboard_readers, "_load_patient_sessions", return_value=[session]), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[wound]), \
             patch.object(dashboard_readers, "_load_patient_educations", return_value=[edu]):
            # Force cache miss
            dashboard_readers.ttl_cache.clear()
            result = dashboard_readers.get_patient_timeline("U-merged", days=14)

        types = [e["type"] for e in result["events"]]
        # Newest first → education, wound, session, symptom
        self.assertEqual(types, ["education", "wound", "teleconsult", "symptom"])
        self.assertEqual(result["symptom_count"], 1)
        self.assertEqual(result["session_count"], 1)
        self.assertEqual(result["wound_count"], 1)
        self.assertEqual(result["education_count"], 1)
        self.assertEqual(result["latest_risk_level"], "high")

        # Wound event has expected fields
        wound_ev = next(e for e in result["events"] if e["type"] == "wound")
        self.assertEqual(wound_ev["severity"], "medium")
        self.assertEqual(wound_ev["observations"], ["มีหนอง", "บวมแดง"])
        self.assertAlmostEqual(wound_ev["confidence"], 0.85)

        # Education event has resolved Thai label
        edu_ev = next(e for e in result["events"] if e["type"] == "education")
        self.assertEqual(edu_ev["topic"], "wound_care")
        self.assertEqual(edu_ev["topic_label"], "การดูแลแผล")
        self.assertTrue(edu_ev["personalized"])

    def test_empty_timeline_includes_new_count_fields(self):
        from services import dashboard_readers
        empty = dashboard_readers._empty_timeline("U-x")
        self.assertEqual(empty["wound_count"], 0)
        self.assertEqual(empty["education_count"], 0)


# -----------------------------------------------------------------------------
# 7. patient.html renders all 4 event types
# -----------------------------------------------------------------------------
from test_dashboard_auth import DashboardAuthTestBase


class PatientPageRenderTests(DashboardAuthTestBase):
    """Reuse existing auth fixture so we get a real bcrypt-hashed user."""

    def _login(self):
        import re
        resp = self.client.get("/dashboard/login")
        csrf = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            resp.get_data(as_text=True),
        ).group(1)
        self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )

    def test_patient_view_renders_all_event_types(self):
        from services import dashboard_readers

        self._login()

        now = datetime.now(tz=LOCAL_TZ)
        events = [
            {
                "type": "education", "type_label": "อ่านความรู้",
                "timestamp": now - timedelta(hours=1),
                "timestamp_label": "26/04/2026 15:00",
                "topic": "wound_care", "topic_label": "การดูแลแผล",
                "source": "RecommendKnowledge", "personalized": True,
            },
            {
                "type": "wound", "type_label": "วิเคราะห์รูปแผล",
                "timestamp": now - timedelta(hours=2),
                "timestamp_label": "26/04/2026 14:00",
                "severity": "high", "observations": ["มีหนอง"],
                "advice": "พบแพทย์ด่วน", "confidence": 0.9,
            },
        ]
        fake_timeline = {
            "user_id": "U-render-test-id-12345",
            "user_id_short": "U-re***12345",
            "symptom_count": 0, "session_count": 0,
            "wound_count": 1, "education_count": 1,
            "latest_risk_level": "high",
            "events": events,
        }

        # Patch the imported reference inside views module (not the source)
        from routes.dashboard import views
        with patch.object(views, "get_patient_timeline", return_value=fake_timeline):
            resp = self.client.get("/dashboard/patient/U-render-test-id-12345")

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Wound markers
        self.assertIn("วิเคราะห์รูปแผล", html)
        self.assertIn("มีหนอง", html)
        self.assertIn("พบแพทย์ด่วน", html)
        # Education markers
        self.assertIn("อ่านความรู้", html)
        self.assertIn("การดูแลแผล", html)
        self.assertIn("เฉพาะราย", html)  # personalized badge
        # Stats cards
        self.assertIn("วิเคราะห์แผล", html)
        self.assertIn("ดูความรู้", html)


if __name__ == "__main__":
    unittest.main()
