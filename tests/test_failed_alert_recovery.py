# -*- coding: utf-8 -*-
"""Regression tests for FailedNurseAlerts manual recovery (KWN-03)."""
from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime
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
        "Last_Attempt_At": "",
        "Resolved_At": "",
        "Resolved_By": "",
    }
    base.update(overrides)
    return base


class FailedAlertRecoveryLogicTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def tearDown(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_retry_success(self):
        from services.dashboard_actions import retry_failed_alert
        from services.cache import ttl_cache

        row = _row(Status="pending", Retry_Count="0")
        with patch("database.failed_nurse_alerts.read_failed_nurse_alert_by_key", return_value=row) as mock_read, \
             patch("services.notification.send_line_push", return_value=True) as mock_push, \
             patch("database.failed_nurse_alerts.update_failed_alert_by_key", return_value=True) as mock_update:
            result = retry_failed_alert("symptom-alert:v1:SECRETKEY", "nurse_kwan")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "ส่งแจ้งเตือนสำเร็จ")
        mock_read.assert_called_once_with("symptom-alert:v1:SECRETKEY")
        mock_push.assert_called_once_with("RAW NOTIFICATION MESSAGE")
        
        # Check updates passed to database
        mock_update.assert_called_once()
        updates = mock_update.call_args[0][1]
        self.assertEqual(updates["Status"], "sent")
        self.assertEqual(updates["Retry_Count"], 1)
        self.assertEqual(updates["Last_Error"], "")
        self.assertIsNotNone(updates["Last_Attempt_At"])

    def test_retry_failure(self):
        from services.dashboard_actions import retry_failed_alert

        row = _row(Status="pending", Retry_Count="2")
        with patch("database.failed_nurse_alerts.read_failed_nurse_alert_by_key", return_value=row), \
             patch("services.notification.send_line_push", return_value=False), \
             patch("database.failed_nurse_alerts.update_failed_alert_by_key", return_value=True) as mock_update:
            result = retry_failed_alert("symptom-alert:v1:SECRETKEY", "nurse_kwan")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "ส่งแจ้งเตือนไม่สำเร็จชั่วคราว")
        
        # Check updates passed to database
        mock_update.assert_called_once()
        updates = mock_update.call_args[0][1]
        self.assertEqual(updates["Status"], "failed")
        self.assertEqual(updates["Retry_Count"], 3)
        self.assertEqual(updates["Last_Error"], "retry_push_failed")

    def test_resolve_success(self):
        from services.dashboard_actions import resolve_failed_alert

        row = _row(Status="failed")
        with patch("database.failed_nurse_alerts.read_failed_nurse_alert_by_key", return_value=row) as mock_read, \
             patch("database.failed_nurse_alerts.update_failed_alert_by_key", return_value=True) as mock_update:
            result = resolve_failed_alert("symptom-alert:v1:SECRETKEY", "nurse_kwan")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "เคลียร์เคสสำเร็จ")
        mock_read.assert_called_once_with("symptom-alert:v1:SECRETKEY")
        
        mock_update.assert_called_once()
        updates = mock_update.call_args[0][1]
        self.assertEqual(updates["Status"], "resolved")
        self.assertEqual(updates["Resolved_By"], "nurse_kwan")
        self.assertIsNotNone(updates["Resolved_At"])

    def test_non_actionable_alert_blocked(self):
        from services.dashboard_actions import retry_failed_alert, resolve_failed_alert

        sent_row = _row(Status="sent")
        resolved_row = _row(Status="resolved")

        # Retry blocked on sent
        with patch("database.failed_nurse_alerts.read_failed_nurse_alert_by_key", return_value=sent_row), \
             patch("database.failed_nurse_alerts.update_failed_alert_by_key") as mock_update:
            result = retry_failed_alert("symptom-alert:v1:SECRETKEY", "nurse_kwan")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "รายการนี้ได้รับการจัดการแล้ว")
        mock_update.assert_not_called()

        # Resolve blocked on resolved
        with patch("database.failed_nurse_alerts.read_failed_nurse_alert_by_key", return_value=resolved_row), \
             patch("database.failed_nurse_alerts.update_failed_alert_by_key") as mock_update:
            result = resolve_failed_alert("symptom-alert:v1:SECRETKEY", "nurse_kwan")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "รายการนี้ได้รับการจัดการแล้ว")
        mock_update.assert_not_called()

    def test_concurrent_retry_blocked_by_lock(self):
        from services.dashboard_actions import retry_failed_alert
        from services.cache import ttl_cache

        # Set lock key
        ttl_cache.set("lock:retry:symptom-alert:v1:SECRETKEY", True, 10)

        with patch("database.failed_nurse_alerts.read_failed_nurse_alert_by_key") as mock_read:
            result = retry_failed_alert("symptom-alert:v1:SECRETKEY", "nurse_kwan")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "กำลังดำเนินการส่งรายการนี้อยู่ กรุณารอสักครู่")
        mock_read.assert_not_called()


class FailedAlertRecoveryRouteTests(unittest.TestCase):

    def setUp(self):
        import bcrypt
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-failed-alerts-recovery"
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

    def test_routes_require_login(self):
        resp = self.client.post("/dashboard/failed-alerts/some-key/retry", data={"csrf_token": "test-csrf"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/login", resp.location)

        resp2 = self.client.post("/dashboard/failed-alerts/some-key/resolve", data={"csrf_token": "test-csrf"})
        self.assertEqual(resp2.status_code, 302)
        self.assertIn("/dashboard/login", resp2.location)

    def test_routes_require_csrf(self):
        self._login_session()
        
        # Missing CSRF
        resp = self.client.post("/dashboard/failed-alerts/some-key/retry", data={})
        self.assertEqual(resp.status_code, 400)

        # Invalid CSRF
        resp2 = self.client.post("/dashboard/failed-alerts/some-key/retry", data={"csrf_token": "wrong"})
        self.assertEqual(resp2.status_code, 400)

    def test_retry_route_success(self):
        self._login_session()
        
        with patch("routes.dashboard.views.retry_failed_alert", return_value=type('ActionResult', (object,), {"ok": True, "message": "ส่งแจ้งเตือนสำเร็จ"})) as mock_action:
            resp = self.client.post("/dashboard/failed-alerts/symptom-alert:v1:SECRETKEY/retry", data={
                "csrf_token": "test-csrf"
            })
        
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/failed-alerts", resp.location)
        mock_action.assert_called_once_with("symptom-alert:v1:SECRETKEY", "nurse_kwan")

    def test_resolve_route_success(self):
        self._login_session()
        
        with patch("routes.dashboard.views.resolve_failed_alert", return_value=type('ActionResult', (object,), {"ok": True, "message": "เคลียร์เคสสำเร็จ"})) as mock_action:
            resp = self.client.post("/dashboard/failed-alerts/symptom-alert:v1:SECRETKEY/resolve", data={
                "csrf_token": "test-csrf"
            })
        
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/failed-alerts", resp.location)
        mock_action.assert_called_once_with("symptom-alert:v1:SECRETKEY", "nurse_kwan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
