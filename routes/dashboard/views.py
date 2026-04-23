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

from flask import render_template, request

from config import get_logger
from routes.dashboard import dashboard_bp
from services.auth import (
    current_nurse,
    get_csrf_token,
    require_nurse_auth,
)
from services.dashboard_readers import (
    get_home_stats,
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
    return render_template("_queue_table.html", queue=items)


@dashboard_bp.route("/partials/alerts", methods=["GET"])
@require_nurse_auth
def alerts_partial():
    """คืน fragment ของ alerts list (ใช้จาก HTMX polling)."""
    days = _parse_int_arg("days", default=7, min_value=1, max_value=30)
    level = request.args.get("level", "medium").strip().lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"
    items = get_recent_alerts(days=days, limit=200, min_risk_level=level)
    return render_template("_alerts_table.html", alerts=items)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
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
