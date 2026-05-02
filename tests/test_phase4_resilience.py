# -*- coding: utf-8 -*-
"""
Phase 4 P4-3: sheet retry + readiness probe + scheduler shutdown.

Coverage:
1. retry_sheet_op succeeds first try → no retries
2. retry_sheet_op recovers after 1 transient failure
3. retry_sheet_op exhausts retries on persistent transient failure
4. retry_sheet_op raises immediately on non-transient (logic) errors
5. is_transient_error: detects gspread / requests / 5xx / 429 / timeout
6. education_logs.save uses retry helper
7. /readyz returns 200 when sheets reachable
8. /readyz returns 503 when sheets unreachable
9. /readyz reports 'skipped' when no creds configured
10. shutdown_scheduler accepts wait kwarg
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database.retry import retry_sheet_op, is_transient_error


class _Transient(Exception):
    """Stand-in for gspread.APIError (matched by name in retry helper)."""
    def __str__(self): return "503 Service Unavailable"


_Transient.__name__ = "APIError"  # matches the gspread class name


# -----------------------------------------------------------------------------
# 1-4. retry_sheet_op behavior
# -----------------------------------------------------------------------------
class RetryHelperTests(unittest.TestCase):

    def setUp(self):
        from services.metrics import reset
        reset()

    def test_first_try_succeeds_no_sleep(self):
        fn = MagicMock(return_value="ok")
        with patch("database.retry.time.sleep") as mock_sleep:
            result = retry_sheet_op(fn, op_name="t1")
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 1)
        mock_sleep.assert_not_called()

    def test_recovers_after_one_transient(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Transient("503 Service Unavailable")
            return "recovered"

        with patch("database.retry.time.sleep"):
            result = retry_sheet_op(fn, op_name="t2", max_attempts=3)

        self.assertEqual(result, "recovered")
        self.assertEqual(calls["n"], 2)

        from services.metrics import snapshot
        snap = snapshot()
        self.assertGreaterEqual(snap.get("sheets.retry.t2.attempt", 0), 1)
        self.assertGreaterEqual(snap.get("sheets.retry.t2.recovered", 0), 1)

    def test_exhausts_on_persistent_transient(self):
        fn = MagicMock(side_effect=_Transient("503"))
        with patch("database.retry.time.sleep"):
            with self.assertRaises(_Transient):
                retry_sheet_op(fn, op_name="t3", max_attempts=3)
        self.assertEqual(fn.call_count, 3)
        from services.metrics import snapshot
        self.assertGreaterEqual(
            snapshot().get("sheets.retry.t3.exhausted", 0), 1,
        )

    def test_non_transient_raises_immediately(self):
        fn = MagicMock(side_effect=ValueError("bug in caller"))
        with patch("database.retry.time.sleep") as mock_sleep:
            with self.assertRaises(ValueError):
                retry_sheet_op(fn, op_name="t4", max_attempts=5)
        self.assertEqual(fn.call_count, 1)  # NOT retried
        mock_sleep.assert_not_called()
        from services.metrics import snapshot
        self.assertGreaterEqual(
            snapshot().get("sheets.retry.t4.non_transient", 0), 1,
        )


# -----------------------------------------------------------------------------
# 5. Transient detection
# -----------------------------------------------------------------------------
class TransientDetectionTests(unittest.TestCase):

    def test_class_name_match(self):
        e = _Transient("anything")
        self.assertTrue(is_transient_error(e))

    def test_message_contains_503(self):
        class FooError(Exception):
            pass
        self.assertTrue(is_transient_error(FooError("503 Service Unavailable")))
        self.assertTrue(is_transient_error(FooError("HTTP 502 Bad Gateway")))
        self.assertTrue(is_transient_error(FooError("429 Too Many Requests")))
        self.assertTrue(is_transient_error(FooError("read timeout occurred")))

    def test_logic_error_not_transient(self):
        self.assertFalse(is_transient_error(ValueError("bad arg")))
        self.assertFalse(is_transient_error(KeyError("missing")))
        self.assertFalse(is_transient_error(TypeError("nope")))


# -----------------------------------------------------------------------------
# 6. education_logs uses retry helper
# -----------------------------------------------------------------------------
class EducationLogRetryWiringTests(unittest.TestCase):

    def test_save_invokes_retry_helper(self):
        from database import education_logs

        fake_sheet = MagicMock()
        with patch.object(education_logs, "_get_or_create_sheet", return_value=fake_sheet), \
             patch.object(education_logs, "retry_sheet_op",
                          wraps=lambda fn, **kw: fn()) as mock_retry:
            ok = education_logs.save_education_view(
                user_id="U-r", topic="wound_care", source="GetKnowledge",
            )
        self.assertTrue(ok)
        mock_retry.assert_called_once()
        self.assertEqual(
            mock_retry.call_args.kwargs.get("op_name"),
            "education_logs.append",
        )


# -----------------------------------------------------------------------------
# 7-9. /readyz route
# -----------------------------------------------------------------------------
class ReadyzTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        os.environ.pop("LINE_CHANNEL_SECRET", None)
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client(), app_module.application

    def test_returns_200_when_sheets_ok(self):
        client, app = self._build_client()
        # Force RUNTIME_CONFIG to claim persistence so the probe runs
        app.config['RUNTIME_CONFIG'] = {"can_persist": True}

        with patch("database.sheets.get_spreadsheet", return_value=MagicMock()):
            resp = client.get("/readyz")

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["sheets"], "ok")

    def test_returns_503_when_sheets_unreachable(self):
        client, app = self._build_client()
        app.config['RUNTIME_CONFIG'] = {"can_persist": True}

        with patch("database.sheets.get_spreadsheet", return_value=None):
            resp = client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        body = resp.get_json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["sheets"], "unavailable")

    def test_returns_503_when_sheets_raises(self):
        client, app = self._build_client()
        app.config['RUNTIME_CONFIG'] = {"can_persist": True}

        with patch("database.sheets.get_spreadsheet", side_effect=RuntimeError("net")):
            resp = client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        body = resp.get_json()
        self.assertIn("error: RuntimeError", body["checks"]["sheets"])

    def test_skips_probe_when_no_creds(self):
        client, app = self._build_client()
        app.config['RUNTIME_CONFIG'] = {"can_persist": False}

        resp = client.get("/readyz")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("skipped", body["checks"]["sheets"])


# -----------------------------------------------------------------------------
# 10. Scheduler graceful shutdown signature
# -----------------------------------------------------------------------------
class SchedulerShutdownTests(unittest.TestCase):

    def test_shutdown_scheduler_accepts_wait_kwarg(self):
        from services import scheduler as sched

        fake = MagicMock()
        fake.running = True
        with patch.object(sched, "scheduler", fake):
            sched.shutdown_scheduler(wait=True)
        fake.shutdown.assert_called_once_with(wait=True)

    def test_shutdown_scheduler_no_op_when_not_running(self):
        from services import scheduler as sched

        fake = MagicMock()
        fake.running = False
        with patch.object(sched, "scheduler", fake):
            sched.shutdown_scheduler()
        fake.shutdown.assert_not_called()

    def test_sigterm_handler_calls_shutdown_with_wait(self):
        from services import scheduler as sched

        with patch.object(sched, "shutdown_scheduler") as mock_shutdown:
            sched._sigterm_handler(15, None)
        mock_shutdown.assert_called_once_with(wait=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
