# -*- coding: utf-8 -*-
"""
Views หลักของ Nurse Dashboard — ทุก route ใน module นี้ต้อง login.

หน้าใน sprint S1-2:
- ``GET /dashboard/``          : Home — แสดงสถิติ + preview คิว/alerts
- ``GET /dashboard/queue``     : รายการคิว teleconsult ทั้งหมด
- ``GET /dashboard/alerts``    : รายการ symptom alert ย้อนหลัง 7 วัน
- ``GET /dashboard/partials/queue``  : HTMX partial สำหรับ refresh เฉพาะตาราง
- ``GET /dashboard/partials/alerts`` : HTMX partial สำหรับ refresh เฉพาะตาราง

หมายเหตุ:
- Route ทุกตัว GET-only และ read-only → ไม่ต้องกังวลเรื่อง CSRF (decorator
  จะตรวจเฉพาะ POST/PUT/PATCH/DELETE อยู่แล้ว).
- ใช้ cache ผ่าน ``services.dashboard_readers`` → ลดโหลด Sheets.
"""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from config import get_logger
from routes.dashboard import dashboard_bp
from services.auth import (
    current_nurse,
    get_csrf_token,
    require_nurse_auth,
    verify_csrf_token,
)
from services.dashboard_actions import (
    assign_nurse_to_session,
    dismiss_alert,
    mark_session_completed,
)
from services.dashboard_readers import (
    get_home_stats,
    get_patient_timeline,
    get_patient_trend,
    get_preconsult_packet,
    get_queue_snapshot,
    get_recent_alerts,
)

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# หน้าเต็ม (full page)
# -----------------------------------------------------------------------------
@dashboard_bp.route("/", methods=["GET"])
@require_nurse_auth
def home():
    """Overview: สถิติ + preview คิว 5 อันแรก + alert 5 อันแรก."""
    stats = get_home_stats()
    queue_preview = get_queue_snapshot(limit=5)
    alerts_preview = get_recent_alerts(days=7, limit=5, min_risk_level="medium")
    return render_template(
        "home.html",
        nurse=current_nurse(),
        csrf_token=get_csrf_token(),
        stats=stats,
        queue=queue_preview,
        alerts=alerts_preview,
    )


@dashboard_bp.route("/queue", methods=["GET"])
@require_nurse_auth
def queue_view():
    """รายการคิวทั้งหมด — เรียงตาม priority แล้วเวลาเข้าคิว."""
    items = get_queue_snapshot(limit=100)
    return render_template(
        "queue.html",
        nurse=current_nurse(),
        csrf_token=get_csrf_token(),
        queue=items,
    )


@dashboard_bp.route("/alerts", methods=["GET"])
@require_nurse_auth
def alerts_view():
    """รายการ alert — filter ด้วย ``?days=`` และ ``?level=`` (high/medium)."""
    days = _parse_int_arg("days", default=7, min_value=1, max_value=30)
    level = request.args.get("level", "medium").strip().lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"
    items = get_recent_alerts(days=days, limit=200, min_risk_level=level)
    return render_template(
        "alerts.html",
        nurse=current_nurse(),
        csrf_token=get_csrf_token(),
        alerts=items,
        filter_days=days,
        filter_level=level,
    )


# -----------------------------------------------------------------------------
# HTMX partials — refresh เฉพาะ fragment โดยไม่ต้อง reload ทั้งหน้า
# -----------------------------------------------------------------------------
@dashboard_bp.route("/partials/queue", methods=["GET"])
@require_nurse_auth
def queue_partial():
    """คืน fragment ของ queue list (ใช้จาก HTMX polling)."""
    items = get_queue_snapshot(limit=100)
    return render_template("_queue_table.html", queue=items, csrf_token=get_csrf_token())


@dashboard_bp.route("/partials/alerts", methods=["GET"])
@require_nurse_auth
def alerts_partial():
    """คืน fragment ของ alerts list (ใช้จาก HTMX polling)."""
    days = _parse_int_arg("days", default=7, min_value=1, max_value=30)
    level = request.args.get("level", "medium").strip().lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"
    items = get_recent_alerts(days=days, limit=200, min_risk_level=level)
    return render_template("_alerts_table.html", alerts=items, csrf_token=get_csrf_token())


# -----------------------------------------------------------------------------
# Notification bell (S1-4)
# -----------------------------------------------------------------------------
@dashboard_bp.route("/partials/bell", methods=["GET"])
@require_nurse_auth
def bell_partial():
    """
    HTMX fragment สำหรับ notification bell — แสดงจำนวนรายการที่ "ต้องสนใจ":
    คิวด่วนมาก (priority=1) + alert วันนี้ที่ยังไม่ dismiss.

    Poll จาก layout ทุก 30s เพื่อให้ badge อัพเดต. รีใช้ ``get_home_stats``
    ที่ cache อยู่แล้ว → ไม่โหลดเพิ่ม.
    """
    stats = get_home_stats()
    total = int(stats.get("queue_high_priority", 0)) + int(stats.get("alerts_today", 0))
    return render_template(
        "_bell.html",
        count=total,
        queue_high=stats.get("queue_high_priority", 0),
        alerts_today=stats.get("alerts_today", 0),
    )


# -----------------------------------------------------------------------------
# Pre-consult preview (S2-1) — HTMX modal partial
# -----------------------------------------------------------------------------
@dashboard_bp.route("/queue/<queue_id>/preview", methods=["GET"])
@require_nurse_auth
def queue_preview(queue_id: str):
    """
    HTMX partial: รวบรวม context ของผู้ป่วยใน queue 1 รายการ
    (อาการล่าสุด, risk profile, reminders, briefing) ให้พยาบาลเห็นก่อนรับเคส.

    การเรียกใช้: ปุ่ม "ดูสรุป" ใน ``_queue_table.html`` ส่ง ``hx-get`` มาที่นี่
    แล้ว swap ผลลัพธ์เข้า ``#preconsult-modal`` ใน ``_layout.html``.
    """
    if not queue_id or len(queue_id) > 64:
        abort(404)
    packet = get_preconsult_packet(queue_id)
    # คืน 200 ทุกกรณีเพื่อให้ HTMX render ข้อความ "ไม่พบ" ได้ — ไม่ต้องการให้
    # client retry หรือแสดง error toast.
    logger.info(
        "preconsult preview nurse=%s queue_id=%s found=%s",
        current_nurse(), queue_id, packet is not None,
    )
    return render_template(
        "_preconsult_modal.html",
        packet=packet,
        csrf_token=get_csrf_token(),
    )


# -----------------------------------------------------------------------------
# Patient detail
# -----------------------------------------------------------------------------
@dashboard_bp.route("/patient/<user_id>", methods=["GET"])
@require_nurse_auth
def patient_view(user_id: str):
    """หน้ารายละเอียดผู้ป่วย 1 คน: timeline ของ symptoms + teleconsult sessions."""
    days = _parse_int_arg("days", default=30, min_value=1, max_value=365)
    # จำกัดความยาว user_id เพื่อกัน URL ยาวผิดปกติ (LINE user ID = 33 chars)
    if not user_id or len(user_id) > 64:
        abort(404)
    timeline = get_patient_timeline(user_id, days=days)
    trend = get_patient_trend(user_id, days=days)
    return render_template(
        "patient.html",
        nurse=current_nurse(),
        csrf_token=get_csrf_token(),
        patient=timeline,
        trend=trend,
        filter_days=days,
    )


# -----------------------------------------------------------------------------
# Write actions (POST) — ทุกตัวต้อง CSRF check + redirect กลับ
# -----------------------------------------------------------------------------
@dashboard_bp.route("/queue/<queue_id>/assign", methods=["POST"])
@require_nurse_auth
def queue_assign(queue_id: str):
    """พยาบาลรับคิว: assign ตัวเองเป็น ``assigned_nurse`` + set status in_progress."""
    _check_csrf()
    nurse = current_nurse() or "unknown"
    result = assign_nurse_to_session(queue_id, nurse)
    flash(result.message, "success" if result.ok else "error")
    return redirect(_safe_next_url(request.form.get("next"), default=url_for("dashboard.queue_view")))


@dashboard_bp.route("/queue/<queue_id>/complete", methods=["POST"])
@require_nurse_auth
def queue_complete(queue_id: str):
    """ปิดเคส: set status completed + notes."""
    _check_csrf()
    nurse = current_nurse() or "unknown"
    notes = (request.form.get("notes") or "").strip()[:500]  # cap notes length
    result = mark_session_completed(queue_id, nurse, notes=notes)
    flash(result.message, "success" if result.ok else "error")
    return redirect(_safe_next_url(request.form.get("next"), default=url_for("dashboard.queue_view")))


@dashboard_bp.route("/alerts/dismiss", methods=["POST"])
@require_nurse_auth
def alerts_dismiss():
    """ซ่อน alert 1 รายการ (in-memory, 24h TTL)."""
    _check_csrf()
    nurse = current_nurse() or "unknown"
    user_id = (request.form.get("user_id") or "").strip()
    timestamp_iso = (request.form.get("timestamp") or "").strip()
    if not user_id or not timestamp_iso:
        flash("ข้อมูล alert ไม่ครบ", "error")
    else:
        result = dismiss_alert(user_id, timestamp_iso, nurse)
        flash(result.message, "success" if result.ok else "error")
    return redirect(_safe_next_url(request.form.get("next"), default=url_for("dashboard.alerts_view")))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _check_csrf() -> None:
    """ตรวจ CSRF token จาก form — 400 ถ้าไม่ผ่าน."""
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not verify_csrf_token(submitted):
        logger.warning("csrf invalid endpoint=%s nurse=%s", request.endpoint, current_nurse())
        abort(400, description="CSRF token invalid")


def _safe_next_url(raw: str | None, default: str) -> str:
    """
    ป้องกัน open redirect: ``next`` ต้องเป็น path ภายในแอป (ขึ้นต้นด้วย ``/``
    และไม่มี scheme/host). มิฉะนั้น fallback default.
    """
    if not raw:
        return default
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return default



def _parse_int_arg(name: str, default: int, min_value: int, max_value: int) -> int:
    """อ่าน query param เป็น int แบบปลอดภัย clamp ตาม range ที่กำหนด."""
    raw = request.args.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return default
    return max(min_value, min(max_value, value))
