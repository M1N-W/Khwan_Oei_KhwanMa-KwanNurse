# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 1 (S1-1): ทดสอบระบบ authentication ของ Nurse Dashboard.

ขอบเขตการทดสอบ:
- Feature flag: ถ้าไม่ตั้ง ``NURSE_DASHBOARD_AUTH`` ต้องคืน 404
- GET /dashboard/login ต้องคืนฟอร์มพร้อม CSRF token
- Password verification (bcrypt) ทำงานถูก — ทั้ง pass และ fail
- Login สำเร็จ → สร้าง session + redirect ไป home
- Login ผิด → แสดง error + ไม่สร้าง session
- Rate limit: 5 failures ต่อ IP ใน 5 นาที → 403
- CSRF: POST ไม่มี token → 400
- Idle timeout: session หมดอายุ → redirect ไป login
- Logout: POST (มี CSRF) → ล้าง session
- Auth gate: เข้า route ใน dashboard โดยไม่ login → redirect ไป login

วิธีรัน::

    python -m unittest test_dashboard_auth.py -v
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ไม่ต้องรัน scheduler ระหว่างเทสต์
os.environ["RUN_SCHEDULER"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _make_hash(password: str) -> str:
    """สร้าง bcrypt hash สำหรับใช้ใน env var ระหว่างเทสต์."""
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")


def _make_auth_env(users: dict[str, str]) -> str:
    """สร้าง env value จาก dict ``{username: plain_password}``."""
    return ",".join(f"{u}:{_make_hash(p)}" for u, p in users.items())


class DashboardAuthTestBase(unittest.TestCase):
    """Base class ที่เตรียม Flask test client + env สำหรับทุกเทสต์."""

    def setUp(self):
        # เตรียม env ที่มีผู้ใช้จริง 1 คนก่อน (แต่ละเทสต์ override ได้)
        os.environ["NURSE_DASHBOARD_AUTH"] = _make_auth_env({"nurse_kwan": "CorrectPass1"})
        os.environ["NURSE_DASHBOARD_SESSION_KEY"] = "test-secret-key-for-unit-tests-only"
        os.environ["NURSE_DASHBOARD_IDLE_MINUTES"] = "15"
        os.environ["DEBUG"] = "true"  # กันไม่ให้ Flask บังคับ Secure cookie (ทำให้ test client ส่ง cookie กลับ)

        # Clear rate limit state จากเทสต์ก่อนหน้า
        from services import auth as auth_module
        with auth_module._login_failures_lock:
            auth_module._login_failures.clear()

        # Rebuild app ใหม่ทุกครั้งเพราะ env เปลี่ยน
        # reload module เพื่อให้ Flask อ่าน DEBUG ใหม่
        import importlib
        import config as _config
        importlib.reload(_config)
        import app as app_module
        importlib.reload(app_module)
        self.flask_app = app_module.application
        self.flask_app.config["TESTING"] = True
        self.client = self.flask_app.test_client()

    def tearDown(self):
        # ลบ env เพื่อไม่ให้ leak ข้าม test
        for key in (
            "NURSE_DASHBOARD_AUTH",
            "NURSE_DASHBOARD_SESSION_KEY",
            "NURSE_DASHBOARD_IDLE_MINUTES",
            "DEBUG",
        ):
            os.environ.pop(key, None)


class FeatureFlagTests(DashboardAuthTestBase):
    """เทสต์ feature flag: dashboard ต้องปิดเมื่อไม่มี env auth."""

    def test_dashboard_returns_404_when_auth_env_missing(self):
        # ลบ env + rebuild app
        os.environ.pop("NURSE_DASHBOARD_AUTH", None)
        import importlib
        import app as app_module
        importlib.reload(app_module)
        client = app_module.application.test_client()

        resp = client.get("/dashboard/login")
        self.assertEqual(resp.status_code, 404)


class LoginFormTests(DashboardAuthTestBase):
    """เทสต์หน้า GET /dashboard/login."""

    def test_get_login_returns_form_with_csrf(self):
        resp = self.client.get("/dashboard/login")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('name="csrf_token"', body)
        self.assertIn("เข้าสู่ระบบ", body)

    def test_get_login_sets_session_cookie_with_csrf(self):
        resp = self.client.get("/dashboard/login")
        # Flask ตั้ง session cookie ใน response header Set-Cookie
        set_cookie_headers = resp.headers.getlist("Set-Cookie")
        self.assertTrue(
            any("session=" in h for h in set_cookie_headers),
            f"คาดว่าจะมี session cookie หลัง GET login; got: {set_cookie_headers}",
        )


class LoginSubmitTests(DashboardAuthTestBase):
    """เทสต์ POST /dashboard/login — ทั้ง success และ fail."""

    def _get_csrf(self) -> str:
        """ดึง CSRF token จากหน้า login form."""
        resp = self.client.get("/dashboard/login")
        body = resp.get_data(as_text=True)
        # หา value ของ csrf_token hidden input
        import re
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', body)
        assert m, "ไม่พบ csrf_token ในหน้า login"
        return m.group(1)

    def test_login_success_redirects_to_home(self):
        csrf = self._get_csrf()
        resp = self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.location.endswith("/dashboard/"))

    def test_login_wrong_password_returns_401_with_error(self):
        csrf = self._get_csrf()
        resp = self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "WrongPass", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("ไม่ถูกต้อง", resp.get_data(as_text=True))

    def test_login_unknown_user_returns_401(self):
        csrf = self._get_csrf()
        resp = self.client.post(
            "/dashboard/login",
            data={"username": "mr_hacker", "password": "whatever", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 401)

    def test_login_missing_csrf_returns_400(self):
        resp = self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_login_rate_limited_after_five_failures(self):
        csrf = self._get_csrf()
        for _ in range(5):
            self.client.post(
                "/dashboard/login",
                data={"username": "nurse_kwan", "password": "wrong", "csrf_token": csrf},
            )
        # ครั้งที่ 6 ต้องโดน rate limit
        resp = self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 403)


class AuthGateTests(DashboardAuthTestBase):
    """เทสต์ decorator: route ที่ต้อง login เข้ามา unauthenticated → redirect."""

    def test_home_unauthenticated_redirects_to_login(self):
        resp = self.client.get("/dashboard/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/login", resp.location)

    def test_home_after_login_returns_200(self):
        # Login ก่อน
        resp_get = self.client.get("/dashboard/login")
        import re
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.get_data(as_text=True)).group(1)
        self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )
        # ขอหน้า home
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("nurse_kwan", resp.get_data(as_text=True))


class IdleTimeoutTests(DashboardAuthTestBase):
    """เทสต์ idle timeout: session หมดอายุ → redirect ไป login."""

    def test_session_expires_after_idle_timeout(self):
        # ตั้ง timeout ต่ำมาก (1 นาที) เพื่อลดเวลาเทสต์
        os.environ["NURSE_DASHBOARD_IDLE_MINUTES"] = "1"

        # Login ก่อน
        resp_get = self.client.get("/dashboard/login")
        import re
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.get_data(as_text=True)).group(1)
        self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )

        # Fake ว่า last_active เก่ากว่า timeout
        from services import auth as auth_module
        with self.client.session_transaction() as sess:
            sess[auth_module._SESSION_LAST_ACTIVE_KEY] = time.time() - 120  # 2 นาทีก่อน

        resp = self.client.get("/dashboard/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/login", resp.location)


class LogoutTests(DashboardAuthTestBase):
    """เทสต์ logout endpoint."""

    def _login(self):
        resp_get = self.client.get("/dashboard/login")
        import re
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.get_data(as_text=True)).group(1)
        self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )
        # หลัง login CSRF token จะถูก regen ใน session ใหม่ — ดึงจาก home
        resp_home = self.client.get("/dashboard/")
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_home.get_data(as_text=True))
        return m.group(1) if m else None

    def test_logout_clears_session_and_redirects(self):
        csrf = self._login()
        self.assertIsNotNone(csrf, "ต้องเจอ CSRF หลัง login")

        resp = self.client.post(
            "/dashboard/logout",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/login", resp.location)

        # ขอ home หลัง logout ต้อง redirect
        resp_home = self.client.get("/dashboard/", follow_redirects=False)
        self.assertEqual(resp_home.status_code, 302)

    def test_logout_without_csrf_returns_400(self):
        self._login()
        resp = self.client.post("/dashboard/logout", data={})
        self.assertEqual(resp.status_code, 400)


class DashboardViewsTests(DashboardAuthTestBase):
    """S1-2: smoke test สำหรับ home / queue / alerts views หลัง login."""

    def _login(self):
        import re
        resp_get = self.client.get("/dashboard/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.get_data(as_text=True)).group(1)
        self.client.post(
            "/dashboard/login",
            data={"username": "nurse_kwan", "password": "CorrectPass1", "csrf_token": csrf},
        )

    def _patch_sheets_empty(self):
        """Mock Sheets ให้ว่าง เพื่อหลีกเลี่ยงการเรียก gspread จริง."""
        from unittest.mock import patch
        p1 = patch("database.sheets.get_worksheet", return_value=None)
        p2 = patch("database.sheets.get_recent_symptom_reports", return_value=[])
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_home_renders_with_empty_data(self):
        self._login()
        self._patch_sheets_empty()
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("ภาพรวมวันนี้", body)
        self.assertIn("คิวปรึกษา", body)

    def test_queue_view_renders(self):
        self._login()
        self._patch_sheets_empty()
        resp = self.client.get("/dashboard/queue")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ยังไม่มีคิว", resp.get_data(as_text=True))

    def test_alerts_view_renders_with_filters(self):
        self._login()
        self._patch_sheets_empty()
        resp = self.client.get("/dashboard/alerts?days=3&level=high")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # ตัวเลือก days=3 ต้อง selected
        self.assertIn('value="3" selected', body)
        # ตัวเลือก level=high ต้อง selected
        self.assertIn('value="high"   selected', body)

    def test_queue_partial_returns_fragment_without_layout(self):
        self._login()
        self._patch_sheets_empty()
        resp = self.client.get("/dashboard/partials/queue")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # Partial ต้องไม่มี <html> tag (เป็น fragment)
        self.assertNotIn("<html", body.lower())

    def test_views_require_login(self):
        # ไม่ login → ทุก route ต้อง redirect ไป login
        for path in ("/dashboard/", "/dashboard/queue", "/dashboard/alerts",
                     "/dashboard/partials/queue", "/dashboard/partials/alerts"):
            resp = self.client.get(path, follow_redirects=False)
            self.assertEqual(resp.status_code, 302, f"path={path}")
            self.assertIn("/dashboard/login", resp.location, f"path={path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
