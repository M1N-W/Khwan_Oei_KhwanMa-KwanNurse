# -*- coding: utf-8 -*-
"""
Phase 2-D regression tests: early-warning trend detection.

Run: python -m unittest test_early_warning.py -v

All external I/O (Google Sheets, LINE push) is mocked. No network calls.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _make_report(days_ago=0, score=0, fever="ไม่มี", wound="ปกติ",
                 risk_level="ปกติ", user_id="u1"):
    from config import LOCAL_TZ
    ts = datetime.now(tz=LOCAL_TZ) - timedelta(days=days_ago, hours=1)
    return {
        "timestamp": ts,
        "user_id": user_id,
        "pain": 0,
        "wound": wound,
        "fever": fever,
        "mobility": "เดินได้",
        "risk_level": risk_level,
        "risk_score": score,
    }


class TrendAnalysisTests(unittest.TestCase):
    """Pure-logic tests for analyze_symptom_trend()."""

    def setUp(self):
        from services.early_warning import _reset_dedup_for_tests
        _reset_dedup_for_tests()

    def test_empty_reports_triggers_nothing(self):
        from services.early_warning import analyze_symptom_trend
        result = analyze_symptom_trend([])
        self.assertFalse(result["triggered"])
        self.assertEqual(result["flags"], [])

    def test_rising_risk_detected_across_three_reports(self):
        from services.early_warning import analyze_symptom_trend
        # newest first: scores 5, 3, 1 → reversed 1→3→5 (rising by >=2)
        reports = [
            _make_report(days_ago=0, score=5),
            _make_report(days_ago=1, score=3),
            _make_report(days_ago=2, score=1),
        ]
        result = analyze_symptom_trend(reports)
        self.assertTrue(result["triggered"])
        self.assertIn("rising_risk", result["flags"])

    def test_flat_scores_do_not_trigger_rising(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=2),
            _make_report(days_ago=1, score=2),
            _make_report(days_ago=2, score=2),
        ]
        result = analyze_symptom_trend(reports)
        self.assertNotIn("rising_risk", result["flags"])

    def test_persistent_fever_over_three_days(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=2, fever="มีไข้"),
            _make_report(days_ago=1, score=2, fever="ตัวร้อน"),
            _make_report(days_ago=2, score=1, fever="ไม่มี"),
        ]
        result = analyze_symptom_trend(reports)
        self.assertIn("persistent_fever", result["flags"])

    def test_fever_negation_not_counted(self):
        """Guard against the 'ไม่มี contains มี' substring trap."""
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=1, fever="ไม่มี"),
            _make_report(days_ago=1, score=1, fever="ไม่มีไข้"),
            _make_report(days_ago=2, score=1, fever="ปกติ"),
        ]
        result = analyze_symptom_trend(reports)
        self.assertNotIn("persistent_fever", result["flags"])

    def test_worsening_wound_from_normal_to_pus(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=3, wound="แผลมีหนอง"),
            _make_report(days_ago=1, score=2, wound="บวมแดง"),
            _make_report(days_ago=2, score=1, wound="ปกติ"),
        ]
        result = analyze_symptom_trend(reports)
        self.assertIn("worsening_wound", result["flags"])

    def test_stable_wound_does_not_trigger(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, wound="ปกติ"),
            _make_report(days_ago=1, wound="ปกติ"),
        ]
        result = analyze_symptom_trend(reports)
        self.assertNotIn("worsening_wound", result["flags"])

    def test_silence_after_high_risk(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=3, score=5, risk_level="⚠️ เสี่ยงสูง"),
        ]
        result = analyze_symptom_trend(reports)
        self.assertIn("silence_after_high_risk", result["flags"])

    def test_recent_high_risk_report_does_not_trigger_silence(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=5),
        ]
        result = analyze_symptom_trend(reports)
        self.assertNotIn("silence_after_high_risk", result["flags"])

    def test_repeated_high_risk_in_five_days(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=4),
            _make_report(days_ago=2, score=3),
            _make_report(days_ago=4, score=1),
        ]
        result = analyze_symptom_trend(reports)
        self.assertIn("repeated_high_risk", result["flags"])

    def test_max_score_reflects_window(self):
        from services.early_warning import analyze_symptom_trend
        reports = [
            _make_report(days_ago=0, score=2),
            _make_report(days_ago=1, score=7),
            _make_report(days_ago=2, score=1),
        ]
        result = analyze_symptom_trend(reports)
        self.assertEqual(result["max_score"], 7)


class PerUserCheckTests(unittest.TestCase):
    """Tests for check_user_early_warning() including notification and dedup."""

    def setUp(self):
        from services.early_warning import _reset_dedup_for_tests
        _reset_dedup_for_tests()

    def test_notifies_nurse_group_on_trigger(self):
        reports = [
            _make_report(days_ago=0, score=5),
            _make_report(days_ago=1, score=3),
            _make_report(days_ago=2, score=1),
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import check_user_early_warning
            analysis = check_user_early_warning("u1")
            self.assertTrue(analysis["triggered"])
            mock_push.assert_called_once()
            msg, target = mock_push.call_args.args[:2]
            self.assertEqual(target, "G123")
            self.assertIn("Early-Warning", msg)

    def test_does_not_notify_when_no_trigger(self):
        reports = [_make_report(days_ago=0, score=1)]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import check_user_early_warning
            analysis = check_user_early_warning("u1")
            self.assertFalse(analysis["triggered"])
            mock_push.assert_not_called()

    def test_dedup_within_same_day(self):
        reports = [
            _make_report(days_ago=0, score=5),
            _make_report(days_ago=1, score=3),
            _make_report(days_ago=2, score=1),
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import check_user_early_warning
            check_user_early_warning("u1")
            check_user_early_warning("u1")  # second call same day
            self.assertEqual(mock_push.call_count, 1)

    def test_no_notify_when_nurse_group_missing(self):
        reports = [
            _make_report(days_ago=0, score=5),
            _make_report(days_ago=1, score=3),
            _make_report(days_ago=2, score=1),
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", ""):
            from services.early_warning import check_user_early_warning
            check_user_early_warning("u1")
            mock_push.assert_not_called()

    def test_returns_none_on_empty_user_id(self):
        from services.early_warning import check_user_early_warning
        self.assertIsNone(check_user_early_warning(""))

    def test_notify_false_suppresses_push(self):
        reports = [
            _make_report(days_ago=0, score=5),
            _make_report(days_ago=1, score=3),
            _make_report(days_ago=2, score=1),
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import check_user_early_warning
            analysis = check_user_early_warning("u1", notify=False)
            self.assertTrue(analysis["triggered"])
            mock_push.assert_not_called()


class ScanTests(unittest.TestCase):
    """Tests for run_early_warning_scan()."""

    def setUp(self):
        from services.early_warning import _reset_dedup_for_tests
        _reset_dedup_for_tests()

    def test_scan_groups_by_user_and_flags_rising(self):
        reports = [
            # user u_rising: rising 1→3→5
            _make_report(user_id="u_rising", days_ago=0, score=5),
            _make_report(user_id="u_rising", days_ago=1, score=3),
            _make_report(user_id="u_rising", days_ago=2, score=1),
            # user u_stable: flat low risk
            _make_report(user_id="u_stable", days_ago=0, score=1),
            _make_report(user_id="u_stable", days_ago=1, score=1),
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import run_early_warning_scan
            flagged = run_early_warning_scan()
            self.assertEqual(flagged, 1)
            self.assertEqual(mock_push.call_count, 1)
            msg = mock_push.call_args.args[0]
            self.assertIn("u_rising", msg)

    def test_scan_empty_window_returns_zero(self):
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=[]):
            from services.early_warning import run_early_warning_scan
            self.assertEqual(run_early_warning_scan(), 0)

    def test_scan_respects_dedup(self):
        reports = [
            _make_report(user_id="u1", days_ago=0, score=5),
            _make_report(user_id="u1", days_ago=1, score=3),
            _make_report(user_id="u1", days_ago=2, score=1),
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push") as mock_push, \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import (
                check_user_early_warning, run_early_warning_scan,
            )
            check_user_early_warning("u1")
            run_early_warning_scan()
            self.assertEqual(mock_push.call_count, 1)


class SheetReaderTests(unittest.TestCase):
    """Smoke tests for database.get_recent_symptom_reports()."""

    def test_empty_sheet_returns_empty_list(self):
        with patch("database.sheets.get_worksheet", return_value=None):
            from database import get_recent_symptom_reports
            self.assertEqual(get_recent_symptom_reports("u1"), [])

    def test_filters_by_user_and_sorts_desc(self):
        from config import LOCAL_TZ
        now = datetime.now(tz=LOCAL_TZ)
        ws = MagicMock()
        ws.get_all_values.return_value = [
            ["timestamp", "user_id", "pain", "wound", "fever", "mobility",
             "risk_level", "risk_score"],
            [(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
             "u1", "2", "ปกติ", "ไม่มี", "เดินได้", "ปกติ", "1"],
            [(now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
             "u2", "3", "หนอง", "มีไข้", "ไม่ได้", "เสี่ยงสูง", "5"],
            [(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
             "u1", "4", "บวมแดง", "มีไข้", "เดินได้", "เสี่ยงสูง", "3"],
        ]
        with patch("database.sheets.get_worksheet", return_value=ws):
            from database import get_recent_symptom_reports
            out = get_recent_symptom_reports("u1", days=7)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["user_id"], "u1")
        # Newest first
        self.assertGreater(out[0]["timestamp"], out[1]["timestamp"])

    def test_excludes_rows_older_than_window(self):
        from config import LOCAL_TZ
        now = datetime.now(tz=LOCAL_TZ)
        ws = MagicMock()
        ws.get_all_values.return_value = [
            ["timestamp", "user_id", "pain", "wound", "fever", "mobility",
             "risk_level", "risk_score"],
            [(now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
             "u1", "2", "ปกติ", "ไม่มี", "เดินได้", "ปกติ", "1"],
        ]
        with patch("database.sheets.get_worksheet", return_value=ws):
            from database import get_recent_symptom_reports
            self.assertEqual(get_recent_symptom_reports("u1", days=7), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
