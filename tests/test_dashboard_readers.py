# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 1 (S1-2): ทดสอบ ``services/dashboard_readers.py``.

ขอบเขต:
- ``get_queue_snapshot``: filter เฉพาะ waiting, sort ตาม priority, คำนวณเวลารอถูก,
  ใช้ cache บนการเรียกครั้งที่ 2.
- ``get_recent_alerts``: filter ตาม risk level, ย้อนหลังภายใน days.
- ``get_home_stats``: นับ queue total/high-priority, alerts_today/alerts_7d ถูก.
- ``invalidate_dashboard_cache``: ลบ entry ที่ขึ้นต้นด้วย ``dash:``.

Run::

    python -m unittest test_dashboard_readers.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _mk_queue_sheet_values(rows):
    """สร้าง values grid แบบที่ gspread.get_all_values คืน — header + rows."""
    header = [
        "Queue_ID", "Timestamp", "Session_ID", "User_ID",
        "Issue_Type", "Priority", "Status", "Estimated_Wait",
    ]
    return [header] + rows


class _FakeSheet:
    """Mock worksheet ที่คืน values ที่เรากำหนด."""

    def __init__(self, values):
        self._values = values

    def get_all_values(self):
        return self._values


# -----------------------------------------------------------------------------
# Queue snapshot tests
# -----------------------------------------------------------------------------
class QueueSnapshotTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def tearDown(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_returns_empty_when_no_sheet(self):
        from services import dashboard_readers

        with patch("database.sheets.get_worksheet", return_value=None):
            result = dashboard_readers.get_queue_snapshot()
        self.assertEqual(result, [])

    def test_filters_only_waiting_status(self):
        from services import dashboard_readers

        ts_recent = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet = _FakeSheet(_mk_queue_sheet_values([
            ["q1", ts_recent, "s1", "UAAA...", "medication", "2", "waiting", "15"],
            ["q2", ts_recent, "s2", "UBBB...", "wound", "1", "completed", "0"],
            ["q3", ts_recent, "s3", "UCCC...", "emergency", "1", "removed", "0"],
        ]))
        with patch("database.sheets.get_worksheet", return_value=sheet):
            result = dashboard_readers.get_queue_snapshot(force_refresh=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["session_id"], "s1")

    def test_sorts_by_priority_then_time(self):
        from services import dashboard_readers
        from config import LOCAL_TZ

        now = datetime.now(tz=LOCAL_TZ)
        old = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        new = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        sheet = _FakeSheet(_mk_queue_sheet_values([
            # priority 2, ใหม่ → ควรมาอันสุดท้าย
            ["q1", new, "s1", "U1", "medication", "2", "waiting", "15"],
            # priority 1, ใหม่ → มาก่อน priority 2
            ["q2", new, "s2", "U2", "emergency", "1", "waiting", "30"],
            # priority 1, เก่ากว่า → ภายใน priority 1 เก่ามาก่อน
            ["q3", old, "s3", "U3", "emergency", "1", "waiting", "30"],
        ]))
        with patch("database.sheets.get_worksheet", return_value=sheet):
            result = dashboard_readers.get_queue_snapshot(force_refresh=True)

        self.assertEqual([r["session_id"] for r in result], ["s3", "s2", "s1"])

    def test_caches_second_call(self):
        from services import dashboard_readers

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet = _FakeSheet(_mk_queue_sheet_values([
            ["q1", ts, "s1", "U1", "med", "2", "waiting", "10"],
        ]))
        with patch("database.sheets.get_worksheet", return_value=sheet) as m:
            dashboard_readers.get_queue_snapshot()
            dashboard_readers.get_queue_snapshot()
            dashboard_readers.get_queue_snapshot()
        # เรียก sheets แค่ครั้งเดียว, อีก 2 ครั้งได้จาก cache
        self.assertEqual(m.call_count, 1)

    def test_force_refresh_bypasses_cache(self):
        from services import dashboard_readers

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet = _FakeSheet(_mk_queue_sheet_values([
            ["q1", ts, "s1", "U1", "med", "2", "waiting", "10"],
        ]))
        with patch("database.sheets.get_worksheet", return_value=sheet) as m:
            dashboard_readers.get_queue_snapshot()
            dashboard_readers.get_queue_snapshot(force_refresh=True)
        self.assertEqual(m.call_count, 2)

    def test_waited_minutes_computed(self):
        from services import dashboard_readers
        from config import LOCAL_TZ

        ts20 = (datetime.now(tz=LOCAL_TZ) - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
        sheet = _FakeSheet(_mk_queue_sheet_values([
            ["q1", ts20, "s1", "U1", "med", "2", "waiting", "15"],
        ]))
        with patch("database.sheets.get_worksheet", return_value=sheet):
            result = dashboard_readers.get_queue_snapshot(force_refresh=True)
        # 20 นาที ± 1 นาที (overhead)
        self.assertGreaterEqual(result[0]["waited_minutes"], 19)
        self.assertLessEqual(result[0]["waited_minutes"], 21)

    def test_user_id_shortened(self):
        from services import dashboard_readers

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        long_uid = "U" + "a" * 32  # ยาว 33 ตัวอักษรเหมือน LINE user ID จริง
        sheet = _FakeSheet(_mk_queue_sheet_values([
            ["q1", ts, "s1", long_uid, "med", "2", "waiting", "10"],
        ]))
        with patch("database.sheets.get_worksheet", return_value=sheet):
            result = dashboard_readers.get_queue_snapshot(force_refresh=True)
        self.assertEqual(result[0]["user_id"], long_uid)
        self.assertIn("…", result[0]["user_id_short"])
        self.assertTrue(len(result[0]["user_id_short"]) < 20)


# -----------------------------------------------------------------------------
# Alerts tests
# -----------------------------------------------------------------------------
class RecentAlertsTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def tearDown(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_filter_min_risk_level_medium(self):
        from services import dashboard_readers
        from config import LOCAL_TZ

        now = datetime.now(tz=LOCAL_TZ)
        sample = [
            {"timestamp": now, "user_id": "U1", "pain": "3", "wound": "ok",
             "fever": "no", "mobility": "ok", "risk_level": "low", "risk_score": 2},
            {"timestamp": now, "user_id": "U2", "pain": "7", "wound": "red",
             "fever": "yes", "mobility": "bad", "risk_level": "medium", "risk_score": 6},
            {"timestamp": now, "user_id": "U3", "pain": "9", "wound": "severe",
             "fever": "yes", "mobility": "no", "risk_level": "high", "risk_score": 9},
        ]
        with patch("database.sheets.get_recent_symptom_reports", return_value=sample):
            result = dashboard_readers.get_recent_alerts(min_risk_level="medium", force_refresh=True)

        self.assertEqual(len(result), 2)
        self.assertEqual({r["risk_level"] for r in result}, {"medium", "high"})

    def test_filter_min_risk_level_high_only(self):
        from services import dashboard_readers
        from config import LOCAL_TZ

        now = datetime.now(tz=LOCAL_TZ)
        sample = [
            {"timestamp": now, "user_id": "U1", "pain": "3", "wound": "ok",
             "fever": "no", "mobility": "ok", "risk_level": "medium", "risk_score": 5},
            {"timestamp": now, "user_id": "U2", "pain": "9", "wound": "severe",
             "fever": "yes", "mobility": "no", "risk_level": "high", "risk_score": 9},
        ]
        with patch("database.sheets.get_recent_symptom_reports", return_value=sample):
            result = dashboard_readers.get_recent_alerts(min_risk_level="high", force_refresh=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["risk_level"], "high")

    def test_cache_hits_on_same_params(self):
        from services import dashboard_readers

        with patch("database.sheets.get_recent_symptom_reports", return_value=[]) as m:
            dashboard_readers.get_recent_alerts(days=7, min_risk_level="medium")
            dashboard_readers.get_recent_alerts(days=7, min_risk_level="medium")
        self.assertEqual(m.call_count, 1)

    def test_cache_miss_on_different_params(self):
        from services import dashboard_readers

        with patch("database.sheets.get_recent_symptom_reports", return_value=[]) as m:
            dashboard_readers.get_recent_alerts(days=7, min_risk_level="medium")
            dashboard_readers.get_recent_alerts(days=30, min_risk_level="medium")
            dashboard_readers.get_recent_alerts(days=7, min_risk_level="high")
        self.assertEqual(m.call_count, 3)


# -----------------------------------------------------------------------------
# Home stats tests
# -----------------------------------------------------------------------------
class HomeStatsTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def tearDown(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_counts_queue_and_alerts(self):
        from services import dashboard_readers
        from config import LOCAL_TZ

        now = datetime.now(tz=LOCAL_TZ)
        ts_now = now.strftime("%Y-%m-%d %H:%M:%S")
        ts_yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        queue_sheet = _FakeSheet(_mk_queue_sheet_values([
            ["q1", ts_now, "s1", "U1", "emergency", "1", "waiting", "30"],
            ["q2", ts_now, "s2", "U2", "med", "2", "waiting", "15"],
            ["q3", ts_now, "s3", "U3", "med", "3", "waiting", "60"],
        ]))

        alerts_sample = [
            {"timestamp": now, "user_id": "U1", "pain": "9", "wound": "",
             "fever": "", "mobility": "", "risk_level": "high", "risk_score": 9},
            {"timestamp": datetime.strptime(ts_yesterday, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ),
             "user_id": "U2", "pain": "7", "wound": "", "fever": "", "mobility": "",
             "risk_level": "medium", "risk_score": 6},
        ]

        with patch("database.sheets.get_worksheet", return_value=queue_sheet), \
             patch("database.sheets.get_recent_symptom_reports", return_value=alerts_sample):
            stats = dashboard_readers.get_home_stats(force_refresh=True)

        self.assertEqual(stats["queue_total"], 3)
        self.assertEqual(stats["queue_high_priority"], 1)  # เฉพาะ priority=1
        self.assertEqual(stats["alerts_7d"], 2)
        self.assertEqual(stats["alerts_today"], 1)  # เฉพาะ today


# -----------------------------------------------------------------------------
# Invalidate tests
# -----------------------------------------------------------------------------
class InvalidateTests(unittest.TestCase):

    def test_invalidate_dashboard_cache_removes_dash_prefix(self):
        from services import dashboard_readers
        from services.cache import ttl_cache

        ttl_cache.clear()
        ttl_cache.set("dash:queue:v1", "a", 60)
        ttl_cache.set("dash:alerts:v1:d=7", "b", 60)
        ttl_cache.set("other:key", "c", 60)

        removed = dashboard_readers.invalidate_dashboard_cache()
        self.assertEqual(removed, 2)
        self.assertIsNone(ttl_cache.get("dash:queue:v1"))
        self.assertEqual(ttl_cache.get("other:key"), "c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
