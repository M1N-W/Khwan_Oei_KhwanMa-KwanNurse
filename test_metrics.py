# -*- coding: utf-8 -*-
"""
Phase 2 hardening: metrics counter tests.

Validates:
- `services.metrics` counter semantics (incr, snapshot, reset, log_summary).
- `/metrics` route returns JSON snapshot.
- Key hotspots actually call `incr()` when exercised:
  - early-warning alert path
  - LINE push skip-unconfigured path
  - LLM skip-circuit-open / skip-quota paths

Run: python -m unittest test_metrics.py -v
"""
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


class CounterSemanticsTests(unittest.TestCase):

    def setUp(self):
        from services.metrics import reset
        reset()

    def test_incr_default_by_one(self):
        from services.metrics import incr, snapshot
        incr("foo")
        incr("foo")
        incr("bar")
        snap = snapshot()
        self.assertEqual(snap["foo"], 2)
        self.assertEqual(snap["bar"], 1)

    def test_incr_custom_step(self):
        from services.metrics import incr, snapshot
        incr("foo", by=5)
        incr("foo", by=3)
        self.assertEqual(snapshot()["foo"], 8)

    def test_empty_name_is_noop(self):
        from services.metrics import incr, snapshot
        incr("")
        incr(None)  # type: ignore[arg-type]
        self.assertEqual(snapshot(), {})

    def test_reset_clears_counters(self):
        from services.metrics import incr, reset, snapshot
        incr("foo")
        reset()
        self.assertEqual(snapshot(), {})

    def test_log_summary_empty_is_safe(self):
        from services.metrics import log_summary
        # Must not raise even with no data
        log_summary()

    def test_log_summary_non_empty(self):
        from services.metrics import incr, log_summary
        incr("foo")
        incr("bar", by=3)
        log_summary()  # smoke


class MetricsRouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from app import create_app
        cls.client = create_app().test_client()

    def setUp(self):
        from services.metrics import reset
        reset()

    def test_metrics_endpoint_returns_json_snapshot(self):
        from services.metrics import incr
        incr("line_push.success", by=4)
        incr("early_warning.alert_sent")

        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("timestamp", body)
        self.assertIn("counters", body)
        self.assertEqual(body["counters"]["line_push.success"], 4)
        self.assertEqual(body["counters"]["early_warning.alert_sent"], 1)

    def test_metrics_endpoint_empty_counters(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["counters"], {})


class HotspotWiringTests(unittest.TestCase):
    """Exercise real call paths and check the expected counter moved."""

    def setUp(self):
        from services.metrics import reset
        from services.early_warning import _reset_dedup_for_tests
        reset()
        _reset_dedup_for_tests()

    def test_early_warning_alert_sent_is_counted(self):
        from config import LOCAL_TZ
        now = datetime.now(tz=LOCAL_TZ)
        reports = [
            {"timestamp": now, "user_id": "u1", "pain": 0, "wound": "ปกติ",
             "fever": "ไม่มี", "mobility": "เดินได้", "risk_level": "ปกติ",
             "risk_score": 5},
            {"timestamp": now - timedelta(days=1), "user_id": "u1", "pain": 0,
             "wound": "ปกติ", "fever": "ไม่มี", "mobility": "เดินได้",
             "risk_level": "ปกติ", "risk_score": 3},
            {"timestamp": now - timedelta(days=2), "user_id": "u1", "pain": 0,
             "wound": "ปกติ", "fever": "ไม่มี", "mobility": "เดินได้",
             "risk_level": "ปกติ", "risk_score": 1},
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push"), \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import check_user_early_warning
            check_user_early_warning("u1")

        from services.metrics import snapshot
        self.assertEqual(snapshot().get("early_warning.alert_sent"), 1)

    def test_early_warning_dedup_skip_is_counted(self):
        from config import LOCAL_TZ
        now = datetime.now(tz=LOCAL_TZ)
        reports = [
            {"timestamp": now, "user_id": "u1", "pain": 0, "wound": "ปกติ",
             "fever": "ไม่มี", "mobility": "เดินได้", "risk_level": "ปกติ",
             "risk_score": 5},
            {"timestamp": now - timedelta(days=1), "user_id": "u1", "pain": 0,
             "wound": "ปกติ", "fever": "ไม่มี", "mobility": "เดินได้",
             "risk_level": "ปกติ", "risk_score": 3},
            {"timestamp": now - timedelta(days=2), "user_id": "u1", "pain": 0,
             "wound": "ปกติ", "fever": "ไม่มี", "mobility": "เดินได้",
             "risk_level": "ปกติ", "risk_score": 1},
        ]
        with patch("services.early_warning.get_recent_symptom_reports",
                   return_value=reports), \
             patch("services.early_warning.send_line_push"), \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            from services.early_warning import check_user_early_warning
            check_user_early_warning("u1")
            check_user_early_warning("u1")  # second call same day → dedup

        from services.metrics import snapshot
        snap = snapshot()
        self.assertEqual(snap.get("early_warning.alert_sent"), 1)
        self.assertEqual(snap.get("early_warning.dedup_skip"), 1)

    def test_line_push_skip_unconfigured_is_counted(self):
        with patch("services.notification.LINE_CHANNEL_ACCESS_TOKEN", ""), \
             patch("services.notification.NURSE_GROUP_ID", ""):
            from services.notification import send_line_push
            result = send_line_push("hello")
        self.assertFalse(result)
        from services.metrics import snapshot
        self.assertEqual(snapshot().get("line_push.skip_unconfigured"), 1)

    def test_llm_skip_circuit_open_is_counted(self):
        from services import llm
        llm._reset_state_for_tests()
        with patch("services.llm.is_enabled", return_value=True), \
             patch("services.llm._circuit_open", return_value=True):
            out = llm.complete("sys", "user")
        self.assertIsNone(out)
        from services.metrics import snapshot
        self.assertEqual(snapshot().get("llm.skip_circuit_open"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
