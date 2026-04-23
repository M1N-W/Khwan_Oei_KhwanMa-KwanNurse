# -*- coding: utf-8 -*-
"""
ระบบยืนยันตัวตนสำหรับ Nurse Dashboard.

ภาพรวม:
- ใช้ Flask session (cookie-based) เก็บสถานะการ login หลัง verify password
  ด้วย bcrypt.
- รายชื่อพยาบาลที่เข้าระบบได้กำหนดผ่าน env var ``NURSE_DASHBOARD_AUTH`` ใน
  รูปแบบ ``username:bcrypt_hash,username2:bcrypt_hash2``.
- มี idle timeout (ค่าเริ่มต้น 15 นาที) — ถ้าพยาบาลไม่มี activity นานกว่านี้
  จะถูกบังคับ logout เพื่อลดความเสี่ยงกรณีวางมือถือทิ้งไว้.
- มี rate limit ต่อ IP: login ผิดเกิน 5 ครั้งใน 5 นาที → บล็อก 403.
- มี CSRF token เก็บใน session + ตรวจทุก POST.

เหตุผลทางความปลอดภัย:
- ข้อมูลในระบบเป็น PHI (Personal Health Information) ของผู้ป่วย → ต้องมี
  auth ที่แข็งพอสมควรแม้ผู้ใช้จะมีแค่ 2-3 คน.
- bcrypt เลือกเพราะรองรับ salt + cost factor + เป็น algorithm ที่
  community ยอมรับสำหรับเก็บ password hash.
- Session cookie ตั้ง ``HttpOnly``, ``Secure``, ``SameSite=Lax`` เพื่อกัน
  XSS และ CSRF ข้าม origin.

วิธีใช้จาก route::

    from services.auth import require_nurse_auth, current_nurse

    @dashboard_bp.route("/queue")
    @require_nurse_auth
    def queue_view():
        nurse = current_nurse()
        ...

การสร้าง bcrypt hash สำหรับใส่ env var: ใช้ ``scripts/make_nurse_hash.py``.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Optional

import bcrypt
from flask import (
    Response,
    abort,
    current_app,
    redirect,
    request,
    session,
    url_for,
)

from config import get_logger
from services.metrics import incr

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# โครงสร้างข้อมูลผู้ใช้
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class NurseUser:
    """ข้อมูลพยาบาลที่โหลดจาก env var (username + bcrypt hash)."""

    username: str
    bcrypt_hash: bytes

    def verify_password(self, password: str) -> bool:
        """ตรวจสอบรหัสผ่านกับ hash. คืน ``True`` ถ้าตรงกัน."""
        if not password:
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), self.bcrypt_hash)
        except (ValueError, TypeError):
            # hash อาจเสียรูป — บันทึก log แล้วถือว่าไม่ผ่าน
            logger.warning("bcrypt verify failed for user=%s (malformed hash)", self.username)
            return False


# -----------------------------------------------------------------------------
# โหลด/parse รายชื่อพยาบาลจาก env
# -----------------------------------------------------------------------------
def parse_nurse_users(raw: str) -> dict[str, NurseUser]:
    """
    แปลง string รูปแบบ ``user1:hash1,user2:hash2`` เป็น dict ของ ``NurseUser``.

    เหตุที่ใช้เครื่องหมาย ``,`` เป็นตัวคั่น user: bcrypt hash ไม่มี ``,`` อยู่ใน
    alphabet (ใช้ `./A-Za-z0-9`) จึงปลอดภัยไม่ชนกัน.

    ถ้า entry ผิดรูปแบบจะข้าม (ไม่ throw) เพื่อไม่ให้ระบบทั้งแอปล่มเพราะ config
    ผิดเล็กน้อย — แต่จะ log warning ไว้.
    """
    users: dict[str, NurseUser] = {}
    if not raw:
        return users

    for token in raw.split(","):
        token = token.strip()
        if not token or ":" not in token:
            continue
        # แยกแค่ ``:`` ตัวแรกเพราะ bcrypt hash อาจมี ``:`` ภายในไม่ได้จริงๆ
        # แต่ทำเผื่อไว้เพื่อความแน่นอน
        username, _, hash_str = token.partition(":")
        username = username.strip()
        hash_str = hash_str.strip()
        if not username or not hash_str:
            logger.warning("Invalid nurse auth entry skipped (missing field)")
            continue
        try:
            bhash = hash_str.encode("utf-8")
            # ทดสอบว่า hash มีรูปแบบ bcrypt จริงไหม — bcrypt ขึ้นต้นด้วย $2
            if not bhash.startswith(b"$2"):
                logger.warning("Invalid nurse auth entry: %s (not bcrypt)", username)
                continue
            users[username] = NurseUser(username=username, bcrypt_hash=bhash)
        except Exception:
            logger.warning("Invalid nurse auth entry skipped: %s", username, exc_info=True)
            continue
    return users


def load_nurse_users() -> dict[str, NurseUser]:
    """โหลดจาก env var ``NURSE_DASHBOARD_AUTH`` — เรียกสดทุกครั้งเพื่อให้ update ง่าย."""
    return parse_nurse_users(os.environ.get("NURSE_DASHBOARD_AUTH", ""))


def is_dashboard_enabled() -> bool:
    """ดู feature flag — ถ้า env var ว่างถือว่าไม่ได้เปิด dashboard."""
    return bool(os.environ.get("NURSE_DASHBOARD_AUTH", "").strip())


# -----------------------------------------------------------------------------
# Session helpers
# -----------------------------------------------------------------------------
_SESSION_USER_KEY = "nurse_user"
_SESSION_LAST_ACTIVE_KEY = "nurse_last_active"
_SESSION_CSRF_KEY = "nurse_csrf"


def _idle_timeout_seconds() -> int:
    """ดึง timeout จาก env (นาที) แปลงเป็นวินาที. Default = 15 นาที."""
    try:
        minutes = int(os.environ.get("NURSE_DASHBOARD_IDLE_MINUTES", "15"))
    except ValueError:
        minutes = 15
    return max(1, minutes) * 60


def current_nurse() -> Optional[str]:
    """คืน username ของพยาบาลที่ login อยู่ หรือ ``None`` ถ้ายังไม่ได้ login / หมดเวลา."""
    username = session.get(_SESSION_USER_KEY)
    if not username:
        return None

    # เช็ค idle timeout
    last_active = session.get(_SESSION_LAST_ACTIVE_KEY, 0)
    if time.time() - last_active > _idle_timeout_seconds():
        # หมดเวลา — เคลียร์ session และคืน None
        logger.info("dashboard session expired for user=%s", username)
        incr("dashboard.session_expired")
        logout_user()
        return None

    return username


def login_user(username: str) -> None:
    """เริ่ม session หลังจาก verify password สำเร็จ."""
    session.clear()
    session[_SESSION_USER_KEY] = username
    session[_SESSION_LAST_ACTIVE_KEY] = time.time()
    session[_SESSION_CSRF_KEY] = secrets.token_urlsafe(32)
    session.permanent = True
    incr("dashboard.login_success")
    logger.info("dashboard login_success user=%s", username)


def logout_user() -> None:
    """จบ session และเคลียร์ข้อมูลทั้งหมด."""
    session.pop(_SESSION_USER_KEY, None)
    session.pop(_SESSION_LAST_ACTIVE_KEY, None)
    session.pop(_SESSION_CSRF_KEY, None)
    session.clear()


def touch_session() -> None:
    """อัพเดทเวลา last-active เพื่อรีเซ็ต idle timer (เรียกในทุก authed request)."""
    if _SESSION_USER_KEY in session:
        session[_SESSION_LAST_ACTIVE_KEY] = time.time()


def get_csrf_token() -> str:
    """
    ดึง CSRF token ของ session ปัจจุบัน. ถ้ายังไม่มีจะสร้างใหม่.

    ใช้ใน template ``<input type="hidden" name="csrf_token" value="{{ csrf_token }}">``.
    """
    token = session.get(_SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_CSRF_KEY] = token
    return token


def verify_csrf_token(submitted: Optional[str]) -> bool:
    """ตรวจ CSRF token ที่ส่งมากับ form/header. ต้อง match กับที่เก็บใน session."""
    expected = session.get(_SESSION_CSRF_KEY)
    if not expected or not submitted:
        return False
    # ใช้ ``secrets.compare_digest`` เพื่อกัน timing attack
    return secrets.compare_digest(expected, submitted)


# -----------------------------------------------------------------------------
# Rate limiting (ต่อ IP, ใน-memory)
# -----------------------------------------------------------------------------
# โครงสร้าง: {ip: [(attempt_epoch, ...), ...]}
_login_failures: dict[str, list[float]] = {}
_login_failures_lock = __import__("threading").Lock()

_RATE_LIMIT_WINDOW_SECONDS = 5 * 60  # 5 นาที
_RATE_LIMIT_MAX_FAILURES = 5


def _client_ip() -> str:
    """
    หา IP ของ client. เคารพ ``X-Forwarded-For`` เพราะ Render เป็น proxy — แต่
    เอาเฉพาะตัวแรก (สุดท้ายในโซ่) และไม่ trust blindly (สำหรับ rate limit
    เท่านั้น ไม่ได้ใช้ authorization).
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def record_login_failure(ip: str) -> None:
    """บันทึกว่า IP นี้ login ผิด 1 ครั้ง."""
    now = time.time()
    with _login_failures_lock:
        attempts = _login_failures.setdefault(ip, [])
        attempts.append(now)
        # ตัดอันเก่ากว่า window ทิ้งเพื่อไม่ให้ dict โตไม่รู้จบ
        cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
        _login_failures[ip] = [t for t in attempts if t > cutoff]


def is_rate_limited(ip: str) -> bool:
    """ดูว่า IP ถูกบล็อกเพราะ login ผิดบ่อยไหม."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _login_failures_lock:
        attempts = _login_failures.get(ip, [])
        recent = [t for t in attempts if t > cutoff]
        # update ใน dict เพื่อไม่ให้ memory leak
        if recent:
            _login_failures[ip] = recent
        else:
            _login_failures.pop(ip, None)
        return len(recent) >= _RATE_LIMIT_MAX_FAILURES


def clear_rate_limit(ip: str) -> None:
    """เคลียร์ประวัติ failure ของ IP (เรียกหลัง login สำเร็จหรือใน test)."""
    with _login_failures_lock:
        _login_failures.pop(ip, None)


# -----------------------------------------------------------------------------
# Decorator
# -----------------------------------------------------------------------------
def require_nurse_auth(view_func: Callable) -> Callable:
    """
    Decorator ใช้ครอบ route ทุกตัวใน dashboard.

    พฤติกรรม:
    - ถ้า dashboard ยังไม่ถูกเปิด (env ไม่มี auth) → 404 (feature-flagged).
    - ถ้ายังไม่ได้ login → redirect ไป login page.
    - ถ้า idle timeout → logout + redirect.
    - POST ต้องมี CSRF token ที่ถูกต้อง.
    - Login สำเร็จ → touch session + run view.
    - นับ metrics ``dashboard.page_view``.
    """

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # Feature flag: dashboard ต้องถูกเปิดก่อน
        if not is_dashboard_enabled():
            abort(404)

        # ตรวจว่า login อยู่ไหม
        username = current_nurse()
        if not username:
            # ไม่ login → ไป login page (เก็บ ``next`` ไว้กลับมาหน้าเดิม)
            next_url = request.full_path if request.method == "GET" else None
            return redirect(url_for("dashboard.login", next=next_url))

        # ตรวจ CSRF เฉพาะ state-changing methods
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            submitted = (
                request.form.get("csrf_token")
                or request.headers.get("X-CSRF-Token")
            )
            if not verify_csrf_token(submitted):
                logger.warning("CSRF failure user=%s path=%s", username, request.path)
                incr("dashboard.csrf_fail")
                abort(400, description="CSRF token invalid")

        touch_session()
        incr("dashboard.page_view")
        return view_func(*args, **kwargs)

    return wrapper


# -----------------------------------------------------------------------------
# Security headers (ใช้ via Flask after_request)
# -----------------------------------------------------------------------------
def apply_security_headers(response: Response) -> Response:
    """
    เพิ่ม security headers ให้ทุก response ของ dashboard.

    - ``X-Frame-Options: DENY`` กัน clickjacking
    - ``X-Content-Type-Options: nosniff`` กัน MIME sniffing
    - ``Referrer-Policy: strict-origin-when-cross-origin`` ลด referrer leak
    - ``Content-Security-Policy`` จำกัดแหล่งที่มาของ script/style
    """
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # CSP: อนุญาต Tailwind CDN + HTMX CDN + inline style (Tailwind JIT) + self
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdn.tailwindcss.com https://unpkg.com 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'",
    )
    return response
