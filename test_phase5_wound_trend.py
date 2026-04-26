# -*- coding: utf-8 -*-
"""
Phase 5 P5-1: patient trend chart data + render.

Coverage:
 1. _extract_pain_score: numeric, '3/10', empty, free text, out-of-range
 2. get_patient_trend: empty user_id → empty trend
 3. get_patient_trend: builds risk + pain + wound series from underlying loaders
 4. get_patient_trend: skips wound rows with unknown severity
 5. get_patient_trend: oldest-first sort
 6. summary aggregates: max/avg risk, pain max, wound counts
 7. cache_hit metric increments on second call
 8. force_refresh bypasses cache
 9. patient page renders trend KPI strip when data exists
10. patient page hides trend section when data_points = 0
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# 1. Pain score extraction
# -----------------------------------------------------------------------------
class PainExtractionTests(unittest.TestCase):

    def test_pain_extraction_cases(self):
        from services.dashboard_readers import _extract_pain_score
        cases = {
            "3":      3,
            "  7  ":  7,
            "10":     10,
            "0":      0,
            "5/10":   5,
            "8/10":   8,
            "":       None,
            "-":      None,
            "ปวดมาก": None,
            "11":     None,   # out of range
            "-1":     None,
            None:     None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=repr(raw)):
                self.assertEqual(_extract_pain_score(raw), expected)


# -----------------------------------------------------------------------------
# 2-8. get_patient_trend
# -----------------------------------------------------------------------------
class PatientTrendTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        from services.metrics import reset
        ttl_cache.clear()
        reset()

    def _ts(self, days_ago: int) -> datetime:
        from config import LOCAL_TZ
        return datetime.now(tz=LOCAL_TZ) - timedelta(days=days_ago)

    def test_empty_user_id_returns_empty_trend(self):
        from services import dashboard_readers
        result = dashboard_readers.get_patient_trend("")
        self.assertEqual(result["risk_series"], [])
        self.assertEqual(result["wound_series"], [])
        self.assertEqual(result["summary"]["data_points"], 0)

    def test_builds_three_series_from_loaders(self):
        from services import dashboard_readers

        symptoms = [
            {"timestamp": self._ts(5), "user_id": "U1", "risk_level": "high",
             "risk_score": 8, "pain": "7"},
            {"timestamp": self._ts(2), "user_id": "U1", "risk_level": "low",
             "risk_score": 3, "pain": "ปวดน้อย"},  # non-numeric pain → skipped
            {"timestamp": self._ts(1), "user_id": "U1", "risk_level": "medium",
             "risk_score": 5, "pain": "4/10"},
        ]
        wounds = [
            {"timestamp": self._ts(4), "user_id": "U1", "severity": "medium",
             "confidence": 0.85},
            {"timestamp": self._ts(3), "user_id": "U1", "severity": "high",
             "confidence": 0.91},
            {"timestamp": self._ts(0), "user_id": "U1", "severity": "unknown",
             "confidence": 0.5},  # unknown severity → skipped
        ]

        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=symptoms), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=wounds):
            trend = dashboard_readers.get_patient_trend("U1", days=30, force_refresh=True)

        self.assertEqual(len(trend["risk_series"]), 3)
        self.assertEqual(len(trend["pain_series"]), 2)  # one row had non-numeric pain
        self.assertEqual(len(trend["wound_series"]), 2)  # 'unknown' skipped

        # Wound severity score mapping: medium=2, high=3
        wound_values = sorted(p["value"] for p in trend["wound_series"])
        self.assertEqual(wound_values, [2, 3])

    def test_series_sorted_oldest_first(self):
        from services import dashboard_readers

        symptoms = [
            {"timestamp": self._ts(1), "risk_level": "low", "risk_score": 2, "pain": "1"},
            {"timestamp": self._ts(7), "risk_level": "high", "risk_score": 9, "pain": "8"},
            {"timestamp": self._ts(3), "risk_level": "medium", "risk_score": 5, "pain": "4"},
        ]
        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=symptoms), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[]):
            trend = dashboard_readers.get_patient_trend("U1", force_refresh=True)

        ts_iso = [p["ts_iso"] for p in trend["risk_series"]]
        self.assertEqual(ts_iso, sorted(ts_iso))  # ascending

    def test_summary_aggregates(self):
        from services import dashboard_readers

        symptoms = [
            {"timestamp": self._ts(5), "risk_level": "high", "risk_score": 8, "pain": "7"},
            {"timestamp": self._ts(3), "risk_level": "medium", "risk_score": 4, "pain": "5"},
            {"timestamp": self._ts(1), "risk_level": "low", "risk_score": 2, "pain": "2"},
        ]
        wounds = [
            {"timestamp": self._ts(4), "severity": "high", "confidence": 0.9},
            {"timestamp": self._ts(2), "severity": "low", "confidence": 0.7},
            {"timestamp": self._ts(0), "severity": "high", "confidence": 0.85},
        ]
        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=symptoms), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=wounds):
            trend = dashboard_readers.get_patient_trend("U1", force_refresh=True)

        s = trend["summary"]
        self.assertEqual(s["risk_max"], 8)
        # avg = (8+4+2)/3 = 4.67 → rounded to 4.7
        self.assertEqual(s["risk_avg"], 4.7)
        self.assertEqual(s["pain_max"], 7)
        self.assertEqual(s["wound_total"], 3)
        self.assertEqual(s["wound_high_count"], 2)
        self.assertEqual(s["data_points"], 6)  # 3 risk + 3 wound

    def test_skips_rows_without_timestamp(self):
        from services import dashboard_readers
        symptoms = [
            {"timestamp": None, "risk_level": "high", "risk_score": 9, "pain": "5"},
            {"timestamp": self._ts(2), "risk_level": "low", "risk_score": 1, "pain": "2"},
        ]
        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=symptoms), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[]):
            trend = dashboard_readers.get_patient_trend("U1", force_refresh=True)
        self.assertEqual(len(trend["risk_series"]), 1)

    def test_cache_hit_on_second_call(self):
        from services import dashboard_readers
        from services.metrics import snapshot

        symptoms = [{"timestamp": self._ts(1), "risk_level": "low",
                     "risk_score": 1, "pain": "1"}]
        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=symptoms) as ms, \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[]):
            dashboard_readers.get_patient_trend("U1", days=30, force_refresh=True)
            dashboard_readers.get_patient_trend("U1", days=30)  # should hit cache

        # Loaders should have been called only ONCE
        self.assertEqual(ms.call_count, 1)
        self.assertGreaterEqual(snapshot().get("dashboard.trend.cache_hit", 0), 1)

    def test_force_refresh_bypasses_cache(self):
        from services import dashboard_readers
        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=[]) as ms, \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[]):
            dashboard_readers.get_patient_trend("U1", force_refresh=True)
            dashboard_readers.get_patient_trend("U1", force_refresh=True)
        self.assertEqual(ms.call_count, 2)


# -----------------------------------------------------------------------------
# 9-10. Patient page integration (uses DashboardAuthTestBase for real login)
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
            data={"username": "nurse_kwan", "password": "CorrectPass1",
                  "csrf_token": csrf},
        )

    def _ts(self, days_ago: int):
        from config import LOCAL_TZ
        return datetime.now(tz=LOCAL_TZ) - timedelta(days=days_ago)

    def test_chart_section_renders_when_data_present(self):
        from services import dashboard_readers
        self._login()

        symptoms = [{"timestamp": self._ts(2), "user_id": "U-test",
                     "risk_level": "high", "risk_score": 8, "pain": "7"}]
        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=symptoms), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[]), \
             patch.object(dashboard_readers, "_load_patient_educations", return_value=[]), \
             patch.object(dashboard_readers, "_load_patient_sessions", return_value=[]):
            dashboard_readers.ttl_cache.clear()
            resp = self.client.get("/dashboard/patient/U-test-patient-12345")

        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("trend-section", body)
        self.assertIn("trend-chart", body)
        self.assertIn("แนวโน้มสุขภาพ", body)
        self.assertIn("Risk สูงสุด", body)
        self.assertIn("chart.umd.min.js", body)
        self.assertIn("adapter-date-fns", body)

    def test_chart_section_hidden_when_no_data(self):
        from services import dashboard_readers
        self._login()

        with patch.object(dashboard_readers, "_load_patient_symptoms", return_value=[]), \
             patch.object(dashboard_readers, "_load_patient_wounds", return_value=[]), \
             patch.object(dashboard_readers, "_load_patient_educations", return_value=[]), \
             patch.object(dashboard_readers, "_load_patient_sessions", return_value=[]):
            dashboard_readers.ttl_cache.clear()
            resp = self.client.get("/dashboard/patient/U-empty-patient-12345")

        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn("trend-section", body)
        self.assertNotIn("trend-chart", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
