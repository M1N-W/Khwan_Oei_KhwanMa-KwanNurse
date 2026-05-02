# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 1 (S1-3): ทดสอบ ``services/dashboard_actions.py``.

ขอบเขต:
- ``assign_nurse_to_session``: success, queue not found, update fail.
- ``mark_session_completed``: success + notes, queue not found.
- ``dismiss_alert``: key stored + ``is_alert_dismissed`` คืน True → alerts filter.
- cache invalidate เรียก หลังทุก action ที่สำเร็จ.

Run::

    python -m unittest test_dashboard_actions.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


class _FakeQueueSheet:
    """Mock queue sheet ที่คืน row ที่ map queue_id → session_id."""

    def __init__(self, rows):
        header = [
            "Queue_ID", "Timestamp", "Session_ID", "User_ID",
            "Issue_Type", "Priority", "Status", "Estimated_Wait",
        ]
        self._values = [header] + rows

    def get_all_values(self):
        return self._values


# -----------------------------------------------------------------------------
# Assign tests
# -----------------------------------------------------------------------------
class AssignNurseTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_assign_success_invalidates_cache(self):
        from services import dashboard_actions
        from services.cache import ttl_cache

        # Preload cache → after success ต้องถูกล้าง
        ttl_cache.set("dash:queue:v1", [{"mock": 1}], 60)

        sheet = _FakeQueueSheet([
            ["q1", "2026-04-24 00:00:00", "S100", "U1", "med", "2", "waiting", "10"],
        ])
        with patch("database.sheets.get_worksheet", return_value=sheet), \
             patch("database.teleconsult.update_session_status", return_value=True) as m_upd, \
             patch("database.teleconsult.remove_from_queue", return_value=True):
            result = dashboard_actions.assign_nurse_to_session("q1", "nurse_kwan")

        self.assertTrue(result.ok)
        self.assertEqual(result.session_id, "S100")
        m_upd.assert_called_once()
        # ตรวจว่า cache ถูก invalidate
        self.assertIsNone(ttl_cache.get("dash:queue:v1"))

    def test_assign_queue_not_found(self):
        from services import dashboard_actions

        sheet = _FakeQueueSheet([
            ["q1", "2026-04-24 00:00:00", "S100", "U1", "med", "2", "waiting", "10"],
        ])
        with patch("database.sheets.get_worksheet", return_value=sheet):
            result = dashboard_actions.assign_nurse_to_session("q_unknown", "nurse_kwan")
        self.assertFalse(result.ok)
        self.assertIn("ไม่พบคิว", result.message)

    def test_assign_update_status_failure(self):
        from services import dashboard_actions

        sheet = _FakeQueueSheet([
            ["q1", "2026-04-24 00:00:00", "S100", "U1", "med", "2", "waiting", "10"],
        ])
        with patch("database.sheets.get_worksheet", return_value=sheet), \
             patch("database.teleconsult.update_session_status", return_value=False), \
             patch("database.teleconsult.remove_from_queue", return_value=True):
            result = dashboard_actions.assign_nurse_to_session("q1", "nurse_kwan")
        self.assertFalse(result.ok)

    def test_assign_missing_params(self):
        from services import dashboard_actions

        self.assertFalse(dashboard_actions.assign_nurse_to_session("", "nurse").ok)
        self.assertFalse(dashboard_actions.assign_nurse_to_session("q1", "").ok)


# -----------------------------------------------------------------------------
# Complete tests
# -----------------------------------------------------------------------------
class CompleteSessionTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_complete_success_with_notes(self):
        from services import dashboard_actions

        sheet = _FakeQueueSheet([
            ["q1", "2026-04-24 00:00:00", "S100", "U1", "med", "2", "waiting", "10"],
        ])
        with patch("database.sheets.get_worksheet", return_value=sheet), \
             patch("database.teleconsult.update_session_status", return_value=True) as m_upd, \
             patch("database.teleconsult.remove_from_queue", return_value=True):
            result = dashboard_actions.mark_session_completed("q1", "nurse_kwan", notes="ให้ยาแก้ปวด")

        self.assertTrue(result.ok)
        call_kwargs = m_upd.call_args.kwargs
        self.assertEqual(call_kwargs.get("notes"), "ให้ยาแก้ปวด")

    def test_complete_queue_not_found(self):
        from services import dashboard_actions

        sheet = _FakeQueueSheet([])
        with patch("database.sheets.get_worksheet", return_value=sheet):
            result = dashboard_actions.mark_session_completed("q_missing", "nurse_kwan")
        self.assertFalse(result.ok)


# -----------------------------------------------------------------------------
# Dismiss alert tests
# -----------------------------------------------------------------------------
class DismissAlertTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_dismiss_stores_key_and_filter_works(self):
        from services import dashboard_actions
        from datetime import datetime as _dt

        ts = _dt(2026, 4, 24, 9, 0, 0)
        result = dashboard_actions.dismiss_alert("U1", "2026-04-24T09:00:00", "nurse_kwan")

        self.assertTrue(result.ok)
        self.assertTrue(dashboard_actions.is_alert_dismissed("U1", ts))
        # user อื่นไม่ถูก mark
        self.assertFalse(dashboard_actions.is_alert_dismissed("U2", ts))

    def test_dismiss_missing_params_returns_error(self):
        from services import dashboard_actions
        self.assertFalse(dashboard_actions.dismiss_alert("", "2026-04-24T09:00:00", "n").ok)
        self.assertFalse(dashboard_actions.dismiss_alert("U1", "", "n").ok)
        self.assertFalse(dashboard_actions.dismiss_alert("U1", "2026-04-24T09:00:00", "").ok)

    def test_dismissed_alerts_filtered_from_reader(self):
        """Regression: ``get_recent_alerts`` ต้องไม่คืน alert ที่ถูก dismiss."""
        from services import dashboard_actions, dashboard_readers
        from config import LOCAL_TZ

        ts_dt = datetime(2026, 4, 24, 10, 0, 0, tzinfo=LOCAL_TZ)
        sample = [
            {"timestamp": ts_dt, "user_id": "U1", "pain": "9", "wound": "",
             "fever": "", "mobility": "", "risk_level": "high", "risk_score": 9},
        ]

        # ก่อน dismiss → เห็น alert
        with patch("database.sheets.get_recent_symptom_reports", return_value=sample):
            alerts = dashboard_readers.get_recent_alerts(force_refresh=True)
        self.assertEqual(len(alerts), 1)

        # หลัง dismiss → ไม่เห็น alert
        dashboard_actions.dismiss_alert("U1", "2026-04-24T10:00:00", "nurse_kwan")
        with patch("database.sheets.get_recent_symptom_reports", return_value=sample):
            alerts_after = dashboard_readers.get_recent_alerts(force_refresh=True)
        self.assertEqual(len(alerts_after), 0)


# -----------------------------------------------------------------------------
# Patient timeline tests
# -----------------------------------------------------------------------------
class PatientTimelineTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_timeline_merges_symptoms_and_sessions_sorted_newest_first(self):
        from services import dashboard_readers
        from config import LOCAL_TZ

        ts_old = datetime(2026, 4, 20, 9, 0, 0, tzinfo=LOCAL_TZ)
        ts_mid = datetime(2026, 4, 22, 12, 0, 0, tzinfo=LOCAL_TZ)
        ts_new = datetime(2026, 4, 24, 8, 0, 0, tzinfo=LOCAL_TZ)

        symptoms = [
            {"timestamp": ts_old, "user_id": "U1", "pain": "3", "wound": "",
             "fever": "", "mobility": "", "risk_level": "low", "risk_score": 2},
            {"timestamp": ts_new, "user_id": "U1", "pain": "9", "wound": "",
             "fever": "", "mobility": "", "risk_level": "high", "risk_score": 9},
        ]

        # Build session sheet (header มาตรฐาน)
        session_header = [
            "Session_ID", "Timestamp", "User_ID", "Issue_Type", "Priority",
            "Status", "Description", "Queue_Position", "Assigned_Nurse",
            "Started_At", "Completed_At", "Notes",
        ]
        session_values = [
            session_header,
            ["S001", ts_mid.strftime("%Y-%m-%d %H:%M:%S"), "U1", "med", "2",
             "completed", "", "1", "nurse_kwan", "", "", "ok"],
        ]

        class _SessionsSheet:
            def get_all_values(self_inner):
                return session_values

        with patch("database.sheets.get_recent_symptom_reports", return_value=symptoms), \
             patch("database.sheets.get_worksheet", return_value=_SessionsSheet()), \
             patch("services.dashboard_readers._load_patient_wounds", return_value=[]), \
             patch("services.dashboard_readers._load_patient_educations", return_value=[]):
            timeline = dashboard_readers.get_patient_timeline("U1", days=30, force_refresh=True)

        self.assertEqual(timeline["user_id"], "U1")
        self.assertEqual(timeline["symptom_count"], 2)
        self.assertEqual(timeline["session_count"], 1)
        self.assertEqual(len(timeline["events"]), 3)
        # เรียง newest first: ts_new (symptom high) → ts_mid (session) → ts_old (symptom low)
        self.assertEqual(timeline["events"][0]["type"], "symptom")
        self.assertEqual(timeline["events"][0]["risk_level"], "high")
        self.assertEqual(timeline["events"][1]["type"], "teleconsult")
        self.assertEqual(timeline["events"][2]["risk_level"], "low")
        # latest_risk ควรเป็น "high"
        self.assertEqual(timeline["latest_risk_level"], "high")

    def test_timeline_empty_user_id(self):
        from services import dashboard_readers
        result = dashboard_readers.get_patient_timeline("")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["symptom_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
