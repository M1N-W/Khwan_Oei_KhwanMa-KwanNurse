# -*- coding: utf-8 -*-
"""
Write-path actions สำหรับ Nurse Dashboard (Sprint 1 S1-3).

หน้าที่:
- ``assign_nurse_to_session``: รับคิว (queue_id) → หา session_id → อัพเดต
  status เป็น ``in_progress`` + ใส่ ``assigned_nurse`` + เอาออกจากคิว.
- ``mark_session_completed``: ปิด session (สถานะ ``completed``) + ลบออกจากคิว
  + บันทึก notes ถ้ามี.
- ``dismiss_alert``: "ซ่อน" alert ออกจาก dashboard (ใช้ใน S1-3 แบบ in-memory
  เท่านั้น — เก็บใน TTL cache 24h เพื่อไม่ต้องเพิ่ม Sheets sheet ใหม่).

หลักการออกแบบ:
- ทุกฟังก์ชันคืน ``ActionResult`` dataclass ที่มี ``ok`` + ``message`` +
  ``session_id`` เพื่อให้ view ตัดสินใจ redirect/flash ได้ง่าย.
- หลังเขียนสำเร็จ **ต้อง** ``invalidate_dashboard_cache()`` เพื่อให้พยาบาล
  เห็นผลลัพธ์ทันที (ไม่ต้องรอ TTL 10-30s).
- Log audit entry (nurse_username + action + target) ทุกครั้งเพื่อตรวจสอบ
  ย้อนหลังได้ (ข้อมูล PHI).
- ห้ามทำ Sheets operation ใน module นี้ตรง ๆ — เรียกผ่าน ``database.teleconsult``
  เพื่อ reuse error handling + schema knowledge ที่มีอยู่.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import LOCAL_TZ, SessionStatus, get_logger
from services.cache import ttl_cache
from services.dashboard_readers import invalidate_dashboard_cache
from services.metrics import incr

logger = get_logger(__name__)

# TTL สำหรับ dismissed alerts — 24 ชั่วโมง พอที่ alert ใหม่จะมาเรื่อย ๆ
# โดย alert เก่าที่ dismiss ไปแล้วไม่โผล่กลับมา แต่ก็ไม่ถาวรเกินไป
# (ถ้าจำเป็นให้คงถาวรใน S1-4 จะย้ายไป Sheets sheet ใหม่)
DISMISSED_ALERT_TTL_SECONDS = 24 * 60 * 60
DISMISSED_ALERT_PREFIX = "dash:dismissed:"


@dataclass(frozen=True)
class ActionResult:
    """ผลลัพธ์มาตรฐานของ write action — view ใช้ตัดสินใจ redirect/flash."""

    ok: bool
    message: str
    session_id: Optional[str] = None


# -----------------------------------------------------------------------------
# Queue → Session lookup
# -----------------------------------------------------------------------------
def _find_session_id_by_queue_id(queue_id: str) -> Optional[str]:
    """
    หา ``session_id`` จาก ``queue_id`` ใน TeleconsultQueue sheet.

    คืน ``None`` ถ้าไม่เจอหรือ sheet ไม่พร้อม. ไม่ใช้ cache เพราะ action
    จะ invalidate ทันทีหลังเสร็จและการ lookup ถี่มาก (ต่อกดปุ่ม 1 ครั้ง).
    """
    try:
        from config import SHEET_TELECONSULT_QUEUE
        from database.sheets import get_worksheet
    except ImportError:
        logger.exception("Failed to import queue dependencies")
        return None

    if not queue_id:
        return None

    try:
        sheet = get_worksheet(SHEET_TELECONSULT_QUEUE)
        if not sheet:
            return None
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return None
        headers = values[0]
        try:
            qid_idx = headers.index("Queue_ID")
            sid_idx = headers.index("Session_ID")
        except ValueError:
            # Fallback ตามลำดับเริ่มต้น
            qid_idx, sid_idx = 0, 2

        for row in values[1:]:
            if len(row) > max(qid_idx, sid_idx) and row[qid_idx] == queue_id:
                return row[sid_idx] or None
        return None
    except Exception:
        logger.exception("Error finding session by queue_id=%s", queue_id)
        return None


# -----------------------------------------------------------------------------
# Actions
# -----------------------------------------------------------------------------
def assign_nurse_to_session(queue_id: str, nurse_username: str) -> ActionResult:
    """
    พยาบาลรับคิว ``queue_id``. เปลี่ยน session เป็น ``in_progress`` + ใส่ชื่อพยาบาล
    + ลบออกจากคิว (status=removed) เพื่อไม่ให้พยาบาลคนอื่นรับซ้ำ.

    คืน ``ActionResult`` — ``ok=False`` ถ้าหา session ไม่เจอ.
    """
    if not queue_id or not nurse_username:
        return ActionResult(ok=False, message="missing queue_id or nurse")

    session_id = _find_session_id_by_queue_id(queue_id)
    if not session_id:
        incr("dashboard.action.assign.not_found")
        logger.warning("assign: queue_id=%s not found", queue_id)
        return ActionResult(ok=False, message="ไม่พบคิวที่ระบุ")

    try:
        from database.teleconsult import remove_from_queue, update_session_status

        ok = update_session_status(
            session_id=session_id,
            new_status=SessionStatus.IN_PROGRESS,
            assigned_nurse=nurse_username,
        )
        if not ok:
            incr("dashboard.action.assign.failed")
            return ActionResult(ok=False, message="อัพเดต session ไม่สำเร็จ", session_id=session_id)

        # เอาออกจากคิว — ถ้าเอาออกไม่ได้ไม่ rollback session (อันตรายกว่า)
        # แค่ log warning แล้ว scheduler/manual จะ cleanup ทีหลัง
        remove_from_queue(session_id)

        invalidate_dashboard_cache()
        incr("dashboard.action.assign.ok")
        logger.info(
            "audit: nurse=%s action=assign queue_id=%s session_id=%s",
            nurse_username, queue_id, session_id,
        )
        return ActionResult(ok=True, message="รับคิวเรียบร้อย", session_id=session_id)

    except Exception:
        logger.exception("Error assigning queue_id=%s", queue_id)
        incr("dashboard.action.assign.error")
        return ActionResult(ok=False, message="เกิดข้อผิดพลาด", session_id=session_id)


def mark_session_completed(
    queue_id: str,
    nurse_username: str,
    notes: str = "",
) -> ActionResult:
    """
    ปิด session (``status=completed``) + ลบออกจากคิว (เผื่อยังค้างอยู่) + บันทึก notes.

    รับ ``queue_id`` แทน ``session_id`` เพื่อให้ template ส่ง field เดียวกันกับ assign.
    """
    if not queue_id or not nurse_username:
        return ActionResult(ok=False, message="missing queue_id or nurse")

    session_id = _find_session_id_by_queue_id(queue_id)
    if not session_id:
        incr("dashboard.action.complete.not_found")
        return ActionResult(ok=False, message="ไม่พบคิวที่ระบุ")

    try:
        from database.teleconsult import remove_from_queue, update_session_status

        ok = update_session_status(
            session_id=session_id,
            new_status=SessionStatus.COMPLETED,
            assigned_nurse=nurse_username,
            notes=notes or None,
        )
        if not ok:
            incr("dashboard.action.complete.failed")
            return ActionResult(ok=False, message="อัพเดต session ไม่สำเร็จ", session_id=session_id)

        remove_from_queue(session_id)
        invalidate_dashboard_cache()
        incr("dashboard.action.complete.ok")
        logger.info(
            "audit: nurse=%s action=complete queue_id=%s session_id=%s notes_len=%d",
            nurse_username, queue_id, session_id, len(notes or ""),
        )
        return ActionResult(ok=True, message="ปิดเคสเรียบร้อย", session_id=session_id)

    except Exception:
        logger.exception("Error completing queue_id=%s", queue_id)
        incr("dashboard.action.complete.error")
        return ActionResult(ok=False, message="เกิดข้อผิดพลาด", session_id=session_id)


def dismiss_alert(user_id: str, timestamp_iso: str, nurse_username: str) -> ActionResult:
    """
    ซ่อน alert (symptom log row) ออกจาก dashboard ชั่วคราว.

    เนื่องจากตอนนี้ยังไม่มี Sheets sheet สำหรับ alert state เราเก็บใน
    ``ttl_cache`` (in-memory, 24h TTL). ข้อจำกัด:
    - ถ้า Render restart process, dismissals จะหายไป (acceptable สำหรับ pilot
      50 คน — alert จะโผล่กลับ แต่พยาบาลกด dismiss อีกครั้งได้).
    - ไม่ sync ระหว่าง worker หลายตัว — ปัจจุบันใช้ ``WEB_CONCURRENCY=1``
      บน Render free → ไม่มีปัญหา. ถ้าเพิ่ม worker ต้องย้ายไป Sheets/Redis.

    Key format: ``dash:dismissed:{user_id}:{timestamp_iso}``
    """
    if not user_id or not timestamp_iso or not nurse_username:
        return ActionResult(ok=False, message="missing parameters")

    key = f"{DISMISSED_ALERT_PREFIX}{user_id}:{timestamp_iso}"
    ttl_cache.set(key, nurse_username, DISMISSED_ALERT_TTL_SECONDS)
    invalidate_dashboard_cache()
    incr("dashboard.action.dismiss.ok")
    logger.info(
        "audit: nurse=%s action=dismiss_alert user_id=%s timestamp=%s",
        nurse_username, user_id, timestamp_iso,
    )
    return ActionResult(ok=True, message="ซ่อน alert แล้ว")


def is_alert_dismissed(user_id: str, timestamp) -> bool:
    """
    เช็คว่า alert นี้ถูก dismiss หรือยัง (ใช้จาก ``dashboard_readers``).

    ``timestamp`` รับ ``datetime`` (อาจเป็น ``None``) — ฟังก์ชันจะ normalize
    เป็น ISO string เดียวกับที่ ``dismiss_alert`` ใช้เป็น key.
    """
    if not user_id or timestamp is None:
        return False
    ts_iso = _alert_timestamp_key(timestamp)
    return ttl_cache.get(f"{DISMISSED_ALERT_PREFIX}{user_id}:{ts_iso}") is not None


def _alert_timestamp_key(timestamp) -> str:
    """
    สร้าง key string คงที่จาก datetime (หรือ string) — ใช้ทั้งตอน dismiss
    และตอนเช็ค. รูปแบบ: ``YYYY-MM-DDTHH:MM:SS`` (ไม่รวม timezone offset
    เพื่อให้ form submit สะดวก).
    """
    if timestamp is None:
        return ""
    if hasattr(timestamp, "strftime"):
        return timestamp.strftime("%Y-%m-%dT%H:%M:%S")
    return str(timestamp).strip()
