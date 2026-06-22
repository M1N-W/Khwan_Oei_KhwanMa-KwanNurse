# -*- coding: utf-8 -*-
"""Read-only dashboard visibility for FailedNurseAlerts."""
from __future__ import annotations

import os
import re
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _row(**overrides):
    base = {
        "Created_At": "2026-06-20 09:00:00",
        "Idempotency_Key": "symptom-alert:v1:SECRETKEY",
        "Event_Type": "symptom_assessment",
        "User_ID": "U12345678901234567890",
        "Risk_Level": "high",
        "Risk_Score": "3",
        "Payload_JSON": '{"secret":"payload"}',
        "Notification_Message": "RAW NOTIFICATION MESSAGE",
        "Status": "pending",
        "Retry_Count": "0",
        "Last_Error": "initial_line_push_failed",
    }
    base.update(overrides)
    return base


class FailedAlertSnapshotTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def tearDown(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_includes_pending_and_failed_excludes_closed_statuses(self):
        from services import dashboard_readers

        rows = [
            _row(Status="pending", User_ID="U1"),
            _row(Status="failed", User_ID="U2"),
            _row(Status="sent", User_ID="U3"),
            _row(Status="resolved", User_ID="U4"),
            _row(Status="cancelled", User_ID="U5"),
        ]

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=rows):
            snapshot = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)

        self.assertEqual(snapshot["actionable_count"], 2)
        self.assertEqual([i["user_id"] for i in snapshot["items"]], ["U1", "U2"])

    def test_sorts_critical_before_high_then_oldest_first(self):
        from services import dashboard_readers

        rows = [
            _row(User_ID="U-high-old", Risk_Level="high", Risk_Score="3", Created_At="2026-06-20 08:00:00"),
            _row(User_ID="U-critical-new", Risk_Level="critical", Risk_Score="5", Created_At="2026-06-20 10:00:00"),
            _row(User_ID="U-critical-old", Risk_Level="critical", Risk_Score="5", Created_At="2026-06-20 07:00:00"),
        ]

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=rows):
            snapshot = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)

        self.assertEqual(
            [i["user_id"] for i in snapshot["items"]],
            ["U-critical-old", "U-critical-new", "U-high-old"],
        )

    def test_malformed_values_default_safely_and_legacy_risk_normalizes(self):
        from services import dashboard_readers

        rows = [
            _row(
                Risk_Level="🚨 อันตราย - ต้องพบแพทย์ทันที!",
                Risk_Score="not-a-number",
                Retry_Count="bad",
                Created_At="not-a-date",
            )
        ]

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=rows):
            snapshot = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)

        item = snapshot["items"][0]
        self.assertEqual(item["risk_level"], "critical")
        self.assertEqual(item["risk_score"], 0)
        self.assertEqual(item["retry_count"], 0)
        self.assertEqual(item["created_at"], "-")

    def test_known_and_unknown_error_labels_are_safe_thai(self):
        from services import dashboard_readers

        rows = [
            _row(User_ID="U1", Last_Error="initial_line_push_failed"),
            _row(User_ID="U2", Last_Error="requests.Timeout: bearer secret"),
        ]

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=rows):
            snapshot = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)

        labels = [i["error_label"] for i in snapshot["items"]]
        self.assertIn("ส่งแจ้งเตือน LINE ไม่สำเร็จ", labels)
        self.assertIn("การส่งแจ้งเตือนไม่สำเร็จ", labels)
        self.assertNotIn("bearer secret", " ".join(labels))

    def test_patient_identity_enriched_and_raw_fields_absent(self):
        from services import dashboard_readers

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=[_row()]), \
             patch("database.patient_profile.read_patient_profile", return_value={
                 "first_name": "สมชาย", "last_name": "ใจดี", "hn": "HN001",
             }):
            snapshot = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)

        item = snapshot["items"][0]
        self.assertEqual(item["patient_label"], "สมชาย ใจดี · HN HN001")
        self.assertNotIn("Payload_JSON", item)
        self.assertNotIn("Notification_Message", item)
        self.assertNotIn("Idempotency_Key", item)

    def test_degraded_and_empty_states_are_distinct(self):
        from services import dashboard_readers

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=None):
            degraded = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)
        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=[]):
            empty = dashboard_readers.get_failed_nurse_alert_snapshot(force_refresh=True)

        self.assertTrue(degraded["degraded"])
        self.assertFalse(empty["degraded"])
        self.assertEqual(empty["actionable_count"], 0)

    def test_cache_hit_and_limit(self):
        from services import dashboard_readers

        rows = [_row(User_ID=f"U{i}") for i in range(3)]
        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=rows) as loader:
            dashboard_readers.get_failed_nurse_alert_snapshot(limit=1)
            second = dashboard_readers.get_failed_nurse_alert_snapshot(limit=2)

        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len(second["items"]), 2)

    def test_limit_enriches_only_returned_slice_without_truncating_cache(self):
        from services import dashboard_readers
        from services.cache import ttl_cache

        rows = [_row(User_ID=f"U{i}") for i in range(3)]

        def identity(user_id):
            return {"patient_label": f"Patient {user_id}"}

        with patch.object(dashboard_readers, "_load_failed_alert_rows", return_value=rows) as loader, \
             patch.object(dashboard_readers, "_identity_for_user", side_effect=identity) as identity_for_user:
            first = dashboard_readers.get_failed_nurse_alert_snapshot(limit=1, force_refresh=True)
            cached = ttl_cache.get(dashboard_readers.CACHE_KEY_FAILED_ALERTS)
            second = dashboard_readers.get_failed_nurse_alert_snapshot(limit=2)

        self.assertEqual(loader.call_count, 1)
        self.assertEqual(identity_for_user.call_count, 3)
        self.assertEqual(first["actionable_count"], 3)
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(len(second["items"]), 2)
        self.assertEqual(second["actionable_count"], 3)
        self.assertEqual([i["patient_label"] for i in second["items"]], ["Patient U0", "Patient U1"])
        self.assertEqual(len(cached["items"]), 3)
        self.assertNotIn("patient_label", cached["items"][0])
        for item in second["items"]:
            self.assertNotIn("Payload_JSON", item)
            self.assertNotIn("Notification_Message", item)
            self.assertNotIn("Idempotency_Key", item)


class FailedAlertRouteTests(unittest.TestCase):

    def setUp(self):
        import bcrypt
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-failed-alerts"
        os.environ["NURSE_LOGIN_MAX_ATTEMPTS"] = "100"
        hashed = bcrypt.hashpw(b"CorrectPass1", bcrypt.gensalt(rounds=4)).decode("utf-8")
        os.environ["NURSE_DASHBOARD_AUTH"] = f"nurse_kwan:{hashed}"

        import importlib
        import app as app_module
        importlib.reload(app_module)
        self.app = app_module.application
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _login_session(self):
        with self.client.session_transaction() as sess:
            sess["nurse_user"] = "nurse_kwan"
            sess["nurse_last_active"] = time.time()
            sess["nurse_csrf"] = "test-csrf"

    def _snapshot(self, **overrides):
        now = datetime.now()
        item = {
            "created_at": "20/06 09:00",
            "created_at_full": "2026-06-20 09:00",
            "age_minutes": 15,
            "user_id": "U12345678901234567890",
            "user_id_short": "U123…7890",
            "risk_level": "critical",
            "risk_label": "วิกฤต",
            "risk_score": 5,
            "status": "pending",
            "status_label": "รอตรวจสอบ",
            "retry_count": 0,
            "error_label": "ส่งแจ้งเตือน LINE ไม่สำเร็จ",
            "event_type_label": "รายงานอาการเสี่ยง",
            "patient_label": "สมชาย ใจดี · HN HN001",
        }
        snapshot = {
            "items": [item],
            "pending_count": 1,
            "failed_count": 0,
            "actionable_count": 1,
            "degraded": False,
            "refreshed_at": now.strftime("%H:%M:%S"),
        }
        snapshot.update(overrides)
        return snapshot

    def _failed_metric_section(self, body: str) -> str:
        match = re.search(
            r'<section class="stats-rail" aria-label="ตัวชี้วัดการส่งแจ้งเตือน">(.*?)</section>',
            body,
            re.S,
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def test_full_page_requires_login(self):
        resp = self.client.get("/dashboard/failed-alerts", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/login", resp.location)

    def test_full_page_and_partial_render_item_without_raw_payload(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot",
                          return_value=self._snapshot()):
            full = self.client.get("/dashboard/failed-alerts")
            partial = self.client.get("/dashboard/partials/failed-alerts")

        for resp in (full, partial):
            body = resp.get_data(as_text=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("สมชาย ใจดี", body)
            self.assertIn("ส่งแจ้งเตือน LINE ไม่สำเร็จ", body)
            self.assertIn("/dashboard/patient/U12345678901234567890", body)
            self.assertNotIn("Payload_JSON", body)
            self.assertNotIn("RAW NOTIFICATION MESSAGE", body)

    def test_empty_and_degraded_states_render_distinctly(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        empty = self._snapshot(items=[], pending_count=0, failed_count=0, actionable_count=0)
        degraded = self._snapshot(items=[], pending_count=0, failed_count=0,
                                  actionable_count=0, degraded=True)
        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot", return_value=empty):
            empty_resp = self.client.get("/dashboard/partials/failed-alerts")
        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot", return_value=degraded):
            degraded_resp = self.client.get("/dashboard/partials/failed-alerts")

        self.assertIn("ยังไม่มีรายการแจ้งพยาบาลที่ส่งไม่สำเร็จ", empty_resp.get_data(as_text=True))
        self.assertIn("ไม่สามารถตรวจสอบรายการส่งแจ้งเตือนล้มเหลวได้ชั่วคราว",
                      degraded_resp.get_data(as_text=True))

    def test_full_page_degraded_metrics_are_unknown_but_valid_empty_metrics_are_zero(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        empty = self._snapshot(items=[], pending_count=0, failed_count=0, actionable_count=0)
        degraded = self._snapshot(items=[], pending_count=0, failed_count=0,
                                  actionable_count=0, degraded=True)

        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot", return_value=degraded):
            degraded_resp = self.client.get("/dashboard/failed-alerts")
        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot", return_value=empty):
            empty_resp = self.client.get("/dashboard/failed-alerts")

        degraded_body = degraded_resp.get_data(as_text=True)
        empty_body = empty_resp.get_data(as_text=True)
        degraded_metrics = self._failed_metric_section(degraded_body)
        empty_metrics = self._failed_metric_section(empty_body)

        self.assertIn("ไม่สามารถตรวจสอบรายการส่งแจ้งเตือนล้มเหลวได้ชั่วคราว", degraded_body)
        self.assertGreaterEqual(degraded_metrics.count("&mdash;"), 3)
        self.assertGreaterEqual(degraded_metrics.count("ตรวจสอบไม่ได้"), 3)
        self.assertNotRegex(degraded_metrics, r'<p class="metric-value">\s*0\s*</p>')
        self.assertIn("ยังไม่มีรายการแจ้งพยาบาลที่ส่งไม่สำเร็จ", empty_body)
        self.assertEqual(len(re.findall(r'<p class="metric-value">\s*0\s*</p>', empty_metrics)), 3)

    def test_navigation_and_mobile_navigation_include_failed_page(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        with patch.object(dashboard_views, "get_home_stats", return_value={
            "queue_total": 0,
            "queue_high_priority": 0,
            "alerts_today": 0,
            "alerts_7d": 0,
            "failed_alerts_actionable": 0,
            "failed_alerts_pending": 0,
            "failed_alerts_failed": 0,
            "failed_alerts_degraded": False,
            "refreshed_at": "09:00:00",
        }), \
             patch.object(dashboard_views, "get_queue_snapshot", return_value=[]), \
             patch.object(dashboard_views, "get_recent_alerts", return_value=[]):
            resp = self.client.get("/dashboard/")

        body = resp.get_data(as_text=True)
        self.assertGreaterEqual(body.count("การส่งล้มเหลว"), 2)
        self.assertIn("/dashboard/failed-alerts", body)

    def test_home_metric_and_bell_breakdown_include_failed_delivery_separately(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        stats = {
            "queue_total": 0,
            "queue_high_priority": 1,
            "alerts_today": 2,
            "alerts_7d": 4,
            "failed_alerts_actionable": 3,
            "failed_alerts_pending": 2,
            "failed_alerts_failed": 1,
            "failed_alerts_degraded": False,
            "refreshed_at": "09:00:00",
        }
        with patch.object(dashboard_views, "get_home_stats", return_value=stats), \
             patch.object(dashboard_views, "get_queue_snapshot", return_value=[]), \
             patch.object(dashboard_views, "get_recent_alerts", return_value=[]):
            home = self.client.get("/dashboard/")
            bell = self.client.get("/dashboard/partials/bell")

        home_body = home.get_data(as_text=True)
        bell_body = bell.get_data(as_text=True)
        self.assertIn("การส่งแจ้งเตือนค้าง", home_body)
        self.assertTrue(re.search(r">\s*3\s*<", home_body))
        self.assertIn("คิวด่วน 1", bell_body)
        self.assertIn("แจ้งเตือนวันนี้ 2", bell_body)
        self.assertIn("ส่งล้มเหลว 3", bell_body)
        self.assertIn("การส่งล้มเหลว 3 รายการ", bell_body)
        self.assertTrue(re.search(r">\s*6\s*<", bell_body))

    def test_degraded_home_and_bell_do_not_report_failed_delivery_as_zero(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        stats = {
            "queue_total": 0,
            "queue_high_priority": 1,
            "alerts_today": 2,
            "alerts_7d": 4,
            "failed_alerts_actionable": 0,
            "failed_alerts_pending": 0,
            "failed_alerts_failed": 0,
            "failed_alerts_degraded": True,
            "refreshed_at": "09:00:00",
        }
        with patch.object(dashboard_views, "get_home_stats", return_value=stats), \
             patch.object(dashboard_views, "get_queue_snapshot", return_value=[]), \
             patch.object(dashboard_views, "get_recent_alerts", return_value=[]):
            home = self.client.get("/dashboard/")
            bell = self.client.get("/dashboard/partials/bell")

        home_body = home.get_data(as_text=True)
        bell_body = bell.get_data(as_text=True)
        self.assertIn("&mdash;", home_body)
        self.assertIn("ตรวจสอบไม่ได้ชั่วคราว", home_body)
        self.assertIn("การส่งล้มเหลว: ตรวจสอบไม่ได้", bell_body)
        self.assertIn("ไม่สามารถตรวจสอบรายการส่งล้มเหลวได้", bell_body)
        self.assertNotIn("ส่งล้มเหลว 0", bell_body)
        self.assertTrue(re.search(r">\s*3\s*<", bell_body))

    def test_fresh_valid_alert_and_invalid_timestamp_render_distinct_time_text(self):
        self._login_session()
        from routes.dashboard import views as dashboard_views

        fresh = self._snapshot()
        fresh["items"][0]["age_minutes"] = 0
        fresh["items"][0]["created_at_full"] = "2026-06-20 09:00"
        invalid = self._snapshot()
        invalid["items"][0]["age_minutes"] = 0
        invalid["items"][0]["created_at_full"] = ""
        invalid["items"][0]["created_at"] = "-"

        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot", return_value=fresh):
            fresh_resp = self.client.get("/dashboard/partials/failed-alerts")
        with patch.object(dashboard_views, "get_failed_nurse_alert_snapshot", return_value=invalid):
            invalid_resp = self.client.get("/dashboard/partials/failed-alerts")

        self.assertIn("น้อยกว่า 1 นาที", fresh_resp.get_data(as_text=True))
        invalid_body = invalid_resp.get_data(as_text=True)
        self.assertNotIn("น้อยกว่า 1 นาที", invalid_body)
        self.assertRegex(invalid_body, r'title="">\s*-\s*<div class="tiny">-</div>')


if __name__ == "__main__":
    unittest.main(verbosity=2)
