# -*- coding: utf-8 -*-
"""
Blueprint สำหรับ Nurse Dashboard.

โครงสร้าง:
- ``auth_views`` : login/logout endpoints (ไม่ต้องใช้ decorator)
- ``views``      : หน้า home/queue/alerts/patients (ครอบด้วย require_nurse_auth)

การลงทะเบียน blueprint นี้อยู่ใน ``app.py`` หลังจากที่ register_routes หลัก
ทำงานเสร็จแล้ว. ถ้า env var ``NURSE_DASHBOARD_AUTH`` ไม่ได้ตั้ง จะยังลง
blueprint ไว้ แต่ทุก route จะคืน 404 (กันการเข้าถึงโดยไม่ตั้งใจ).
"""
from __future__ import annotations

from flask import Blueprint

from services.auth import apply_security_headers


# URL prefix ``/dashboard`` — ทุก route ของพยาบาลจะขึ้นต้นด้วยเส้นนี้
dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    url_prefix="/dashboard",
    template_folder="templates",
    static_folder="static",
)


@dashboard_bp.after_request
def _add_security_headers(response):
    """เพิ่ม security headers ให้ทุก response ของ blueprint นี้."""
    return apply_security_headers(response)


# Import submodules เพื่อให้ route ถูก register เข้า blueprint
# (วางหลัง blueprint init เพื่อหลีก circular import)
from routes.dashboard import auth_views  # noqa: E402, F401


__all__ = ["dashboard_bp"]
