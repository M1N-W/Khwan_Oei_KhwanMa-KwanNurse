# -*- coding: utf-8 -*-
"""
Routes สำหรับเข้า-ออกระบบ Nurse Dashboard.

- ``GET /dashboard/login``  : แสดงฟอร์ม login
- ``POST /dashboard/login`` : ตรวจ username/password และสร้าง session
- ``POST /dashboard/logout``: จบ session

ไม่ครอบด้วย ``require_nurse_auth`` เพราะเป็น endpoint ก่อน/หลังการ login.
"""
from __future__ import annotations

from flask import (
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from config import get_logger
from routes.dashboard import dashboard_bp
from services.auth import (
    _client_ip,
    clear_rate_limit,
    current_nurse,
    get_csrf_token,
    is_dashboard_enabled,
    is_rate_limited,
    load_nurse_users,
    login_user,
    logout_user,
    record_login_failure,
    verify_csrf_token,
)
from services.metrics import incr

logger = get_logger(__name__)


def _guard_feature_flag() -> None:
    """ถ้า dashboard ไม่ถูกเปิด → 404 ทันที."""
    if not is_dashboard_enabled():
        abort(404)


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    """แสดงฟอร์ม login (GET) หรือรับการ submit (POST)."""
    _guard_feature_flag()

    # ถ้า login อยู่แล้ว → redirect ไป home
    if current_nurse():
        return redirect(url_for("dashboard.home"))

    ip = _client_ip()
    error: str | None = None

    if request.method == "POST":
        # ตรวจ rate limit ก่อนทุกอย่าง
        if is_rate_limited(ip):
            logger.warning("dashboard login rate-limited ip=%s", ip)
            incr("dashboard.login_rate_limited")
            abort(403, description="มีความพยายาม login ผิดเกินกำหนด กรุณารอ 5 นาที")

        # ตรวจ CSRF (token ถูกสร้างใน GET ก่อนหน้า)
        submitted_csrf = request.form.get("csrf_token", "")
        if not verify_csrf_token(submitted_csrf):
            logger.warning("dashboard login CSRF fail ip=%s", ip)
            incr("dashboard.csrf_fail")
            abort(400, description="CSRF token invalid")

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        users = load_nurse_users()
        user = users.get(username)

        if user and user.verify_password(password):
            clear_rate_limit(ip)
            login_user(username)
            # ถ้ามี ``next`` query string กลับไปหน้านั้น มิเช่นนั้นกลับหน้า home
            next_url = request.args.get("next") or url_for("dashboard.home")
            # กันการ redirect ข้าม origin — อนุญาตเฉพาะ path ภายในเว็บเรา
            if not next_url.startswith("/"):
                next_url = url_for("dashboard.home")
            return redirect(next_url)
        else:
            record_login_failure(ip)
            incr("dashboard.login_fail")
            logger.info("dashboard login fail user=%s ip=%s", username, ip)
            error = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"

    # GET หรือ POST ที่ไม่สำเร็จ — render form
    return (
        render_template(
            "login.html",
            error=error,
            csrf_token=get_csrf_token(),
        ),
        401 if error else 200,
    )


@dashboard_bp.route("/logout", methods=["POST"])
def logout():
    """จบ session ของพยาบาล (ต้องผ่าน POST + CSRF)."""
    _guard_feature_flag()

    # ต้องมี CSRF เพื่อกัน attacker ส่ง logout cross-site
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not verify_csrf_token(submitted):
        abort(400, description="CSRF token invalid")

    username = current_nurse()
    if username:
        logger.info("dashboard logout user=%s", username)
        incr("dashboard.logout")
    logout_user()
    return redirect(url_for("dashboard.login"))


# Placeholder home เพื่อให้ login redirect มี target (views.py เต็มจะทำใน S1-2)
@dashboard_bp.route("/", methods=["GET"])
def home():
    """
    หน้าแรก placeholder — S1-2 จะเขียน view เต็มพร้อม queue + alerts.
    ตอนนี้แค่ยืนยันว่า auth ทำงานถูก.
    """
    from services.auth import require_nurse_auth

    # ใช้ decorator แบบ runtime เพื่อหลีกเลี่ยงการเรียก current_nurse นอก context
    @require_nurse_auth
    def _view():
        return render_template(
            "home_placeholder.html",
            nurse=current_nurse(),
            csrf_token=get_csrf_token(),
        )

    return _view()
