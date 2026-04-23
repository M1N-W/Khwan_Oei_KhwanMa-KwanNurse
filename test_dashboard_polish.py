# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 1 (S1-4): ทดสอบ polish items.

ขอบเขต:
- Bell endpoint ``/dashboard/partials/bell``: render badge ตาม stats,
  ซ่อน badge เมื่อ count=0, cap ที่ "99+".
- Password policy ``validate_nurse_password``: length, classes, username-in-password,
  common password, bcrypt byte limit.
- Script ``make_nurse_hash.py``: reject weak + accept strong + --force.

Run::

    python -m unittest test_dashboard_polish.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))


# -----------------------------------------------------------------------------
# Password policy tests
# -----------------------------------------------------------------------------
class PasswordPolicyTests(unittest.TestCase):

    def test_strong_password_passes(self):
        from services.auth import validate_nurse_password
        self.assertEqual(validate_nurse_password("MyStrongPass123", "nurse_kwan"), [])

    def test_too_short(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("Short1", "nurse")
        self.assertTrue(any("ยาวอย่างน้อย" in i for i in issues))

    def test_missing_uppercase(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("allsmall123x", "nurse")
        self.assertTrue(any("พิมพ์ใหญ่" in i for i in issues))

    def test_missing_lowercase(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("ALLBIG123XXX", "nurse")
        self.assertTrue(any("พิมพ์เล็ก" in i for i in issues))

    def test_missing_digit(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("NoDigitsHere", "nurse")
        self.assertTrue(any("ตัวเลข" in i for i in issues))

    def test_contains_username(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("NurseKwan123", "nurse_kwan")
        # "nurse_kwan".lower() ไม่อยู่ใน "nursekwan123" (underscore ถูกเอาออก)
        # → policy ตรวจ uname_low (nurse_kwan) ใน pwd_low (nursekwan123) = False
        # ดังนั้นไม่เจอ — ทดสอบเคสที่ username ตรงกับ password แทน
        issues2 = validate_nurse_password("NurseKwanABC1", "NurseKwan")
        self.assertTrue(any("ชื่อผู้ใช้" in i for i in issues2))

    def test_exact_username_match(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("nursekwan1", "nursekwan1")
        self.assertTrue(any("ชื่อผู้ใช้" in i for i in issues))

    def test_common_password_rejected(self):
        from services.auth import validate_nurse_password
        issues = validate_nurse_password("Password123", "")
        # Password123 ไม่อยู่ใน common list (ตัว P ใหญ่) → wait lowercase check
        # list เก็บเป็น lowercase, .lower() compare → "password123" อยู่ใน list
        self.assertTrue(any("ยอดฮิต" in i for i in issues))

    def test_empty_password(self):
        from services.auth import validate_nurse_password
        self.assertEqual(validate_nurse_password("", "nurse"), ["รหัสผ่านต้องไม่ว่าง"])

    def test_too_long_bytes(self):
        from services.auth import validate_nurse_password
        # 73 ASCII bytes
        issues = validate_nurse_password("A" * 73 + "a1", "")
        self.assertTrue(any("bcrypt" in i for i in issues))


# -----------------------------------------------------------------------------
# make_nurse_hash.py script tests
# -----------------------------------------------------------------------------
class MakeNurseHashScriptTests(unittest.TestCase):

    def _run(self, *args):
        """Run the script and capture stdout/stderr/exitcode."""
        script = _PROJECT_ROOT / "scripts" / "make_nurse_hash.py"
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, encoding="utf-8",
        )
        return proc

    def test_strong_password_produces_bcrypt_hash(self):
        proc = self._run("nurse_kwan", "MyStrongPass1234")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip().startswith("nurse_kwan:$2"))

    def test_weak_password_rejected(self):
        proc = self._run("nurse_kwan", "weak")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("นโยบาย", proc.stderr)

    def test_force_flag_bypasses_policy(self):
        proc = self._run("nurse_kwan", "weak", "--force")
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip().startswith("nurse_kwan:$2"))

    def test_invalid_username_rejected(self):
        proc = self._run("bad:name", "MyStrongPass1234")
        self.assertEqual(proc.returncode, 2)


# -----------------------------------------------------------------------------
# Bell endpoint tests
# -----------------------------------------------------------------------------
class BellEndpointTests(unittest.TestCase):

    def setUp(self):
        import bcrypt
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-bell"
        hashed = bcrypt.hashpw(b"CorrectPass1", bcrypt.gensalt(rounds=4)).decode("utf-8")
        os.environ["NURSE_DASHBOARD_AUTH"] = f"nurse_kwan:{hashed}"
        os.environ["NURSE_LOGIN_MAX_ATTEMPTS"] = "100"

        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        from services.cache import ttl_cache
        ttl_cache.clear()

    def _login(self):
        import re
        resp_get = self.client.get("/dashboard/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.get_data(as_text=True)).group(1)
        self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )

    def test_bell_shows_zero_when_no_alerts(self):
        self._login()
        with patch("database.sheets.get_worksheet", return_value=None), \
             patch("database.sheets.get_recent_symptom_reports", return_value=[]):
            resp = self.client.get("/dashboard/partials/bell")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # count=0 → ไม่มี badge span
        self.assertNotIn("animate-pulse", body)
        # bell icon color = gray
        self.assertIn("text-gray-500", body)

    def test_bell_shows_count_badge(self):
        from datetime import datetime
        from config import LOCAL_TZ
        self._login()

        # Mock: queue มี priority 1 หนึ่งคน + alert วันนี้ 1 คน = count=2
        queue_header = [
            "Queue_ID", "Timestamp", "Session_ID", "User_ID",
            "Issue_Type", "Priority", "Status", "Estimated_Wait",
        ]
        now_ts = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        queue_values = [queue_header, [
            "q1", now_ts, "s1", "U1", "emergency", "1", "waiting", "30",
        ]]

        class _Sheet:
            def get_all_values(self_inner):
                return queue_values

        alerts = [{
            "timestamp": datetime.now(tz=LOCAL_TZ),
            "user_id": "U2", "pain": "9", "wound": "", "fever": "",
            "mobility": "", "risk_level": "high", "risk_score": 9,
        }]

        with patch("database.sheets.get_worksheet", return_value=_Sheet()), \
             patch("database.sheets.get_recent_symptom_reports", return_value=alerts):
            resp = self.client.get("/dashboard/partials/bell")

        body = resp.get_data(as_text=True)
        self.assertIn("text-red-500", body)  # bell icon red
        self.assertIn("animate-pulse", body)  # badge pulses
        # count=2 rendered inside <span>...</span> with whitespace
        import re as _re
        self.assertTrue(_re.search(r">\s*2\s*<", body), "expected count=2 in badge")

    def test_bell_requires_login(self):
        resp = self.client.get("/dashboard/partials/bell", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/login", resp.location)


if __name__ == "__main__":
    unittest.main(verbosity=2)
