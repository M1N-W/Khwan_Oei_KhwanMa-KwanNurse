# -*- coding: utf-8 -*-
"""
Dashboard data readers สำหรับ Nurse Dashboard.

จุดประสงค์:
- รวบรวม read-only query ที่ dashboard ต้องใช้เป็น module เดียว เพื่อให้ view
  ไม่ต้องคุยตรงกับ ``database.*`` (ลด coupling) และใส่ TTL cache ได้จุดเดียว.
- ทุก reader ใน module นี้ "ห้ามเขียน Sheets" — เขียนคือหน้าที่ของ
  ``services.teleconsult`` ฯลฯ. ถ้าต้องเขียน เช่น assign nurse, จะถูกย้ายไปอยู่
  ใน ``services/dashboard_actions.py`` ใน S1-3.

โครงสร้าง cache key:
- ``dash:queue:v1``         → snapshot ของคิว teleconsult (TTL 10s)
- ``dash:alerts:v1:d={d}``  → รายการ high-risk symptom ใน ``d`` วันหลังสุด (TTL 30s)
- ``dash:stats:v1``         → ตัวเลขรวม home (TTL 15s)

TTL สั้นเพราะ:
- พยาบาลต้องการเห็นข้อมูลใกล้ real-time เมื่อมีคนไข้เพิ่มคิวเข้ามา
- คนเข้า dashboard พร้อมกันน่าจะไม่เกิน 2-3 คน → ไม่จำเป็นต้อง cache นาน
- Google Sheets API quota กว้าง (300 requests/min/project) → 10-30s TTL เพียงพอ
  กันยิงถี่

หมายเหตุ:
- หาก credentials ไม่ถูกตั้ง (ระหว่าง dev/test) reader ทุกตัวจะคืนค่า empty
  ที่ view สามารถ render ได้โดยไม่ crash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from config import LOCAL_TZ, get_logger
from services.cache import ttl_cache
from services.metrics import incr

logger = get_logger(__name__)

# -----------------------------------------------------------------------------
# Cache keys และ TTL — ตั้งค่ากลางเพื่อให้ S1-3 invalidate ได้สะดวก
# -----------------------------------------------------------------------------
CACHE_KEY_QUEUE = "dash:queue:v1"
CACHE_KEY_STATS = "dash:stats:v1"
CACHE_KEY_ALERTS_PREFIX = "dash:alerts:v1"
CACHE_KEY_PRECONSULT_PREFIX = "dash:preconsult:v1"

TTL_QUEUE_SECONDS = 10
TTL_ALERTS_SECONDS = 30
TTL_STATS_SECONDS = 15
TTL_PRECONSULT_SECONDS = 30


# -----------------------------------------------------------------------------
# Data classes — ชัดเจน typed, แปลงง่ายเป็น dict ให้ Jinja render
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class QueueItem:
    """1 แถวในคิว teleconsult สำหรับแสดงใน dashboard."""

    queue_id: str
    session_id: str
    user_id: str
    issue_type: str
    priority: int  # 1=high, 2=medium, 3=low
    status: str
    waited_minutes: int  # นับจาก timestamp ถึงปัจจุบัน
    estimated_wait_minutes: int
    queued_at: Optional[datetime]

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "user_id_short": _short_user_id(self.user_id),
            "issue_type": self.issue_type,
            "priority": self.priority,
            "priority_label": _priority_label(self.priority),
            "status": self.status,
            "waited_minutes": self.waited_minutes,
            "estimated_wait_minutes": self.estimated_wait_minutes,
            "queued_at": self.queued_at.strftime("%H:%M") if self.queued_at else "",
            "queued_at_full": self.queued_at.strftime("%Y-%m-%d %H:%M") if self.queued_at else "",
        }


@dataclass(frozen=True)
class AlertItem:
    """1 แถว alert (symptom high risk หรือ early-warning) สำหรับ dashboard."""

    timestamp: Optional[datetime]
    user_id: str
    risk_level: str  # "high" / "medium" / "low"
    risk_score: int
    pain: str
    wound: str
    fever: str
    mobility: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.strftime("%d/%m %H:%M") if self.timestamp else "",
            "timestamp_full": self.timestamp.strftime("%Y-%m-%d %H:%M") if self.timestamp else "",
            "age_minutes": _age_minutes(self.timestamp),
            "user_id": self.user_id,
            "user_id_short": _short_user_id(self.user_id),
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "pain": self.pain or "-",
            "wound": self.wound or "-",
            "fever": self.fever or "-",
            "mobility": self.mobility or "-",
        }


@dataclass(frozen=True)
class HomeStats:
    """ตัวเลขรวมที่แสดงบนหน้า home — snapshot ณ ช่วงเวลา."""

    queue_total: int
    queue_high_priority: int
    alerts_today: int
    alerts_7d: int
    refreshed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_total": self.queue_total,
            "queue_high_priority": self.queue_high_priority,
            "alerts_today": self.alerts_today,
            "alerts_7d": self.alerts_7d,
            "refreshed_at": self.refreshed_at.strftime("%H:%M:%S"),
        }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _short_user_id(user_id: str) -> str:
    """LINE user ID ยาว 33 ตัวอักษร → แสดง 4 ตัวแรก + 4 ตัวท้าย เพื่อประหยัดพื้นที่."""
    if not user_id or len(user_id) < 10:
        return user_id or "-"
    return f"{user_id[:4]}…{user_id[-4:]}"


def _priority_label(priority: int) -> str:
    """แปลง priority number → label ภาษาไทย."""
    return {1: "ด่วนมาก", 2: "ปานกลาง", 3: "ทั่วไป"}.get(priority, "ทั่วไป")


def _age_minutes(ts: Optional[datetime]) -> int:
    """จำนวนนาทีตั้งแต่ ``ts`` ถึงปัจจุบัน — ใช้แสดง 'ผ่านมากี่นาทีแล้ว'."""
    if not ts:
        return 0
    delta = datetime.now(tz=LOCAL_TZ) - ts
    return max(0, int(delta.total_seconds() // 60))


def _parse_queue_timestamp(raw: str) -> Optional[datetime]:
    """แปลง ``YYYY-mm-dd HH:MM:SS`` → datetime with ``LOCAL_TZ``."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=LOCAL_TZ)
    except (ValueError, TypeError):
        return None


# -----------------------------------------------------------------------------
# Public readers — cache-aware
# -----------------------------------------------------------------------------
def get_queue_snapshot(limit: int = 50, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    รายการคิว teleconsult สำหรับ dashboard (newest waiting first, ตาม priority).

    Args:
        limit: จำนวนแถวสูงสุดที่จะคืน (default 50 — มากพอสำหรับ pilot 50 คน).
        force_refresh: ถ้า True จะข้าม cache และดึงจาก Sheets สด.

    Returns:
        list[dict]: ข้อมูลแต่ละรายการผ่าน ``QueueItem.to_dict``. Empty list ถ้า
        credentials ยังไม่ตั้ง หรือ cache miss + read error.
    """
    if not force_refresh:
        cached = ttl_cache.get(CACHE_KEY_QUEUE)
        if cached is not None:
            incr("dashboard.cache_hit.queue")
            return cached[:limit]

    incr("dashboard.cache_miss.queue")
    items = _load_queue_from_sheets()
    serialized = [item.to_dict() for item in items]
    ttl_cache.set(CACHE_KEY_QUEUE, serialized, TTL_QUEUE_SECONDS)
    return serialized[:limit]


def get_recent_alerts(
    days: int = 7,
    limit: int = 50,
    *,
    min_risk_level: str = "medium",
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    รายการ alert (symptom report ที่ risk สูง) ย้อนหลัง ``days`` วัน.

    Args:
        days: ช่วงย้อนหลัง (default 7).
        limit: แถวสูงสุด (newest first).
        min_risk_level: เกณฑ์ต่ำสุดที่นับเป็น alert. ``"low"`` = ทุกแถว,
            ``"medium"`` = medium+high, ``"high"`` = เฉพาะ high.
        force_refresh: ข้าม cache.

    Returns:
        list[dict]: ข้อมูล alert แต่ละแถวผ่าน ``AlertItem.to_dict``.
    """
    key = f"{CACHE_KEY_ALERTS_PREFIX}:d={days}:m={min_risk_level}"
    if not force_refresh:
        cached = ttl_cache.get(key)
        if cached is not None:
            incr("dashboard.cache_hit.alerts")
            return cached[:limit]

    incr("dashboard.cache_miss.alerts")
    items = _load_alerts_from_sheets(days=days, min_risk_level=min_risk_level)
    serialized = [item.to_dict() for item in items]
    ttl_cache.set(key, serialized, TTL_ALERTS_SECONDS)
    return serialized[:limit]


def get_home_stats(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    ตัวเลขรวมสำหรับหน้า home (queue count + alert count).

    ใช้ ``get_queue_snapshot`` และ ``get_recent_alerts`` ภายใน — ซึ่งจะ hit
    cache ถ้าถูกเรียกก่อนหน้า → cheap.
    """
    if not force_refresh:
        cached = ttl_cache.get(CACHE_KEY_STATS)
        if cached is not None:
            incr("dashboard.cache_hit.stats")
            return cached

    incr("dashboard.cache_miss.stats")

    queue = get_queue_snapshot(limit=200)
    alerts_7d = get_recent_alerts(days=7, limit=500, min_risk_level="medium")

    today = datetime.now(tz=LOCAL_TZ).date()
    alerts_today = sum(
        1
        for a in alerts_7d
        if a.get("timestamp_full") and a["timestamp_full"].startswith(today.strftime("%Y-%m-%d"))
    )

    stats = HomeStats(
        queue_total=len(queue),
        queue_high_priority=sum(1 for q in queue if q.get("priority") == 1),
        alerts_today=alerts_today,
        alerts_7d=len(alerts_7d),
        refreshed_at=datetime.now(tz=LOCAL_TZ),
    ).to_dict()

    ttl_cache.set(CACHE_KEY_STATS, stats, TTL_STATS_SECONDS)
    return stats


def get_patient_timeline(
    user_id: str,
    days: int = 30,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Timeline ของผู้ป่วย 1 คน: รวม symptom reports + teleconsult sessions
    ย้อนหลัง ``days`` วัน → sort newest first.

    Returns:
        dict มี key:
            - ``user_id``: str
            - ``user_id_short``: str
            - ``symptom_count``, ``session_count``: int
            - ``events``: list ของ event dict (type + timestamp + details)
            - ``latest_risk_level``: str ล่าสุดของ user
    """
    if not user_id:
        return _empty_timeline("")

    key = f"dash:patient:v1:{user_id}:d={days}"
    if not force_refresh:
        cached = ttl_cache.get(key)
        if cached is not None:
            incr("dashboard.cache_hit.patient")
            return cached

    incr("dashboard.cache_miss.patient")

    symptoms = _load_patient_symptoms(user_id, days)
    sessions = _load_patient_sessions(user_id, limit=50)

    events: list[dict[str, Any]] = []
    for s in symptoms:
        ts = s.get("timestamp")
        events.append({
            "type": "symptom",
            "type_label": "รายงานอาการ",
            "timestamp": ts,
            "timestamp_label": ts.strftime("%d/%m/%Y %H:%M") if ts else "",
            "risk_level": (s.get("risk_level") or "").lower(),
            "risk_score": int(s.get("risk_score") or 0),
            "pain": s.get("pain") or "-",
            "wound": s.get("wound") or "-",
            "fever": s.get("fever") or "-",
            "mobility": s.get("mobility") or "-",
        })
    for sess in sessions:
        ts = sess.get("timestamp")
        events.append({
            "type": "teleconsult",
            "type_label": "ปรึกษาทางไกล",
            "timestamp": ts,
            "timestamp_label": ts.strftime("%d/%m/%Y %H:%M") if ts else "",
            "session_id": sess.get("session_id") or "",
            "issue_type": sess.get("issue_type") or "",
            "status": sess.get("status") or "",
            "assigned_nurse": sess.get("assigned_nurse") or "",
            "notes": sess.get("notes") or "",
        })

    # Newest first; events ที่ไม่มี timestamp → ล่างสุด
    events.sort(key=lambda e: e["timestamp"] or datetime.min.replace(tzinfo=LOCAL_TZ),
                reverse=True)

    latest_risk = ""
    for ev in events:
        if ev["type"] == "symptom" and ev.get("risk_level"):
            latest_risk = ev["risk_level"]
            break

    result = {
        "user_id": user_id,
        "user_id_short": _short_user_id(user_id),
        "symptom_count": len(symptoms),
        "session_count": len(sessions),
        "latest_risk_level": latest_risk,
        "events": events,
    }
    ttl_cache.set(key, result, 30)  # TTL 30s — patient view ไม่เปิดค้างนาน
    return result


def _empty_timeline(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "user_id_short": _short_user_id(user_id),
        "symptom_count": 0,
        "session_count": 0,
        "latest_risk_level": "",
        "events": [],
    }


def _load_patient_symptoms(user_id: str, days: int) -> list[dict[str, Any]]:
    """อ่าน SymptomLog ของ user 1 คนย้อนหลัง ``days`` วัน."""
    try:
        from database.sheets import get_recent_symptom_reports
        return get_recent_symptom_reports(user_id=user_id, days=days, limit=100)
    except Exception:
        logger.exception("Error loading patient symptoms user_id=%s", user_id)
        return []


def _load_patient_sessions(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    อ่านทุก TeleconsultSessions ของ user 1 คน (ไม่กรอง status — แสดงทั้ง
    queued/in_progress/completed/cancelled เพื่อดูประวัติ).
    """
    try:
        from config import SHEET_TELECONSULT_SESSIONS
        from database.sheets import get_worksheet
    except ImportError:
        return []

    try:
        sheet = get_worksheet(SHEET_TELECONSULT_SESSIONS)
        if not sheet:
            return []
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return []

        headers = values[0]

        def col(name: str, default: int) -> int:
            return headers.index(name) if name in headers else default

        idx_sid = col("Session_ID", 0)
        idx_ts = col("Timestamp", 1)
        idx_uid = col("User_ID", 2)
        idx_issue = col("Issue_Type", 3)
        idx_status = col("Status", 5)
        idx_nurse = col("Assigned_Nurse", 8)
        idx_notes = col("Notes", 11)

        out: list[dict[str, Any]] = []
        for row in values[1:]:
            if len(row) <= idx_uid:
                continue
            if row[idx_uid] != user_id:
                continue
            ts = _parse_queue_timestamp(row[idx_ts] if len(row) > idx_ts else "")
            out.append({
                "session_id": row[idx_sid] if len(row) > idx_sid else "",
                "timestamp": ts,
                "user_id": row[idx_uid],
                "issue_type": row[idx_issue] if len(row) > idx_issue else "",
                "status": row[idx_status] if len(row) > idx_status else "",
                "assigned_nurse": row[idx_nurse] if len(row) > idx_nurse else "",
                "notes": row[idx_notes] if len(row) > idx_notes else "",
            })
        out.sort(key=lambda s: s["timestamp"] or datetime.min.replace(tzinfo=LOCAL_TZ),
                 reverse=True)
        return out[:limit]
    except Exception:
        logger.exception("Error loading patient sessions user_id=%s", user_id)
        return []


def invalidate_dashboard_cache() -> int:
    """
    ลบ cache ของ dashboard views (queue/alerts/stats/patient/preconsult) เรียกหลังเขียนข้อมูล.

    **สำคัญ:** ไม่ลบ ``dash:dismissed:*`` (state การ dismiss alert) เพราะต้องการ
    ให้อยู่ครบ 24 ชั่วโมงแม้มีการ write action อื่นเข้ามา. เดิมเรา invalidate
    ด้วย prefix ``dash:`` เดียวจะล้าง dismissal ด้วย — เป็นบั๊ก.

    Returns:
        int: จำนวน cache entry ที่ถูกลบรวมจากทุก prefix ที่ invalidate.
    """
    total = 0
    for prefix in (
        "dash:queue:",
        "dash:alerts:",
        "dash:stats:",
        "dash:patient:",
        "dash:preconsult:",
    ):
        total += ttl_cache.invalidate_prefix(prefix)
    logger.info("Invalidated %d dashboard cache entries", total)
    return total


# -----------------------------------------------------------------------------
# Sheets loaders — แยกออกเพื่อให้ mock ง่ายใน test
# -----------------------------------------------------------------------------
def _load_queue_from_sheets() -> list[QueueItem]:
    """
    อ่าน TeleconsultQueue sheet + filter เฉพาะ Status=waiting,
    sort ตาม priority asc (1 มาก่อน) แล้วตาม queued_at asc (เก่ามาก่อน).
    """
    # Import ภายในฟังก์ชันเพื่อหลีก circular import ระหว่าง services → database → config
    try:
        from database.sheets import get_worksheet
        from config import SHEET_TELECONSULT_QUEUE, QueueStatus
    except ImportError:
        logger.exception("Failed to import database modules for queue reader")
        return []

    try:
        sheet = get_worksheet(SHEET_TELECONSULT_QUEUE)
        if not sheet:
            return []

        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return []

        headers = values[0]
        items: list[QueueItem] = []

        # สร้าง index lookup เพื่ออ่าน column โดยไม่ต้อง hard-code ลำดับ
        def col(name: str, default: int) -> int:
            return headers.index(name) if name in headers else default

        idx_queue_id = col("Queue_ID", 0)
        idx_timestamp = col("Timestamp", 1)
        idx_session_id = col("Session_ID", 2)
        idx_user_id = col("User_ID", 3)
        idx_issue = col("Issue_Type", 4)
        idx_priority = col("Priority", 5)
        idx_status = col("Status", 6)
        idx_estimate = col("Estimated_Wait", 7)

        now = datetime.now(tz=LOCAL_TZ)

        for row in values[1:]:
            if len(row) <= idx_status:
                continue
            status = (row[idx_status] or "").strip()
            if status != QueueStatus.WAITING:
                continue

            try:
                priority = int(str(row[idx_priority]).strip() or 3)
            except (ValueError, TypeError):
                priority = 3

            try:
                estimated = int(str(row[idx_estimate]).strip() or 0) if len(row) > idx_estimate else 0
            except (ValueError, TypeError):
                estimated = 0

            queued_at = _parse_queue_timestamp(row[idx_timestamp] if len(row) > idx_timestamp else "")
            waited = max(0, int((now - queued_at).total_seconds() // 60)) if queued_at else 0

            items.append(
                QueueItem(
                    queue_id=(row[idx_queue_id] if len(row) > idx_queue_id else "") or "",
                    session_id=(row[idx_session_id] if len(row) > idx_session_id else "") or "",
                    user_id=(row[idx_user_id] if len(row) > idx_user_id else "") or "",
                    issue_type=(row[idx_issue] if len(row) > idx_issue else "") or "",
                    priority=priority,
                    status=status,
                    waited_minutes=waited,
                    estimated_wait_minutes=estimated,
                    queued_at=queued_at,
                )
            )

        # Sort: priority น้อยมาก่อน (1=ด่วน), แล้วคิวเก่ามาก่อน
        items.sort(key=lambda x: (x.priority, x.queued_at or now))
        return items

    except Exception:
        logger.exception("Error loading queue from Sheets")
        return []


_RISK_RANK = {"low": 1, "medium": 2, "high": 3}


def _load_alerts_from_sheets(
    days: int = 7,
    min_risk_level: str = "medium",
) -> list[AlertItem]:
    """
    อ่าน SymptomLog + filter เฉพาะ risk_level ≥ ``min_risk_level`` ใน ``days`` วัน.

    ใช้ ``database.sheets.get_recent_symptom_reports`` ที่มีอยู่แล้วเพื่อ
    consistent parsing.
    """
    try:
        from database.sheets import get_recent_symptom_reports
    except ImportError:
        logger.exception("Failed to import get_recent_symptom_reports")
        return []

    # Import ในนี้เพื่อหลีก circular import — dashboard_actions import readers
    from services.dashboard_actions import is_alert_dismissed

    try:
        min_rank = _RISK_RANK.get(min_risk_level.lower(), 2)
        rows = get_recent_symptom_reports(user_id=None, days=days, limit=500)
        items: list[AlertItem] = []
        for r in rows:
            risk = (r.get("risk_level") or "").strip().lower()
            if _RISK_RANK.get(risk, 0) < min_rank:
                continue
            # กรอง alert ที่พยาบาล dismiss ไปแล้ว (เก็บ in-memory 24h)
            if is_alert_dismissed(r.get("user_id") or "", r.get("timestamp")):
                continue
            items.append(
                AlertItem(
                    timestamp=r.get("timestamp"),
                    user_id=r.get("user_id") or "",
                    risk_level=risk or "low",
                    risk_score=int(r.get("risk_score") or 0),
                    pain=r.get("pain") or "",
                    wound=r.get("wound") or "",
                    fever=r.get("fever") or "",
                    mobility=r.get("mobility") or "",
                )
            )
        return items

    except Exception:
        logger.exception("Error loading alerts from Sheets")
        return []


# -----------------------------------------------------------------------------
# Pre-consult packet (S2-1)
# -----------------------------------------------------------------------------
def _issue_label(issue_type: str) -> str:
    """แปลง issue_type code → ชื่อภาษาไทย โดยอิงจาก ``ISSUE_CATEGORIES``."""
    try:
        from config import ISSUE_CATEGORIES
        return ISSUE_CATEGORIES.get(issue_type, {}).get("name_th", issue_type or "อื่น ๆ")
    except Exception:
        return issue_type or "อื่น ๆ"


def _find_queue_row(queue_id: str) -> Optional[dict[str, Any]]:
    """
    หา queue row จาก ``queue_id`` — ใช้ cached snapshot ก่อนลด round trip.
    คืน dict (ตามรูปของ ``QueueItem.to_dict``) หรือ None ถ้าไม่เจอ.
    """
    if not queue_id:
        return None
    snapshot = get_queue_snapshot(limit=500)
    for item in snapshot:
        if item.get("queue_id") == queue_id:
            return item
    return None


def _load_session_description(session_id: str) -> str:
    """อ่าน Description ของ session (ผู้ป่วยพิมพ์ตอน contact-nurse)."""
    if not session_id:
        return ""
    try:
        from config import SHEET_TELECONSULT_SESSIONS
        from database.sheets import get_worksheet
    except ImportError:
        return ""
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_SESSIONS)
        if not sheet:
            return ""
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return ""
        headers = values[0]
        idx_sid = headers.index("Session_ID") if "Session_ID" in headers else 0
        idx_desc = headers.index("Description") if "Description" in headers else 6
        for row in values[1:]:
            if len(row) > idx_sid and row[idx_sid] == session_id:
                return row[idx_desc] if len(row) > idx_desc else ""
        return ""
    except Exception:
        logger.exception("Error loading session description session_id=%s", session_id)
        return ""


def _load_pending_reminders_safe(user_id: str) -> list[dict[str, Any]]:
    """ดึง pending reminders ของ user — never raise."""
    try:
        from database.reminders import get_pending_reminders
    except ImportError:
        return []
    try:
        rows = get_pending_reminders(user_id, None) or []
    except Exception:
        logger.exception("Error loading pending reminders user_id=%s", user_id)
        return []
    out: list[dict[str, Any]] = []
    for r in rows[:5]:  # เอา 5 รายการพอ — modal ไม่ต้องการเยอะ
        out.append({
            "reminder_type": r.get("Reminder_Type") or r.get("reminder_type") or "",
            "scheduled_for": r.get("Scheduled_For") or r.get("scheduled_for") or "",
            "status": r.get("Status") or r.get("status") or "",
        })
    return out


def _load_latest_risk_profile(user_id: str) -> Optional[dict[str, Any]]:
    """อ่าน RiskProfile ล่าสุดของ user — never raise. คืน None ถ้าไม่มี."""
    if not user_id:
        return None
    try:
        from config import SHEET_RISK_PROFILE
        from database.sheets import get_worksheet
    except ImportError:
        return None
    try:
        sheet = get_worksheet(SHEET_RISK_PROFILE)
        if not sheet:
            return None
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return None
        headers = values[0]
        # หา row ล่าสุดของ user_id (สแกนจากท้าย)
        idx_uid = headers.index("User_ID") if "User_ID" in headers else 1
        for row in reversed(values[1:]):
            if len(row) > idx_uid and row[idx_uid] == user_id:
                record = dict(zip(headers, row + [""] * (len(headers) - len(row))))
                return {
                    "age": record.get("Age", ""),
                    "sex": record.get("Sex", ""),
                    "bmi": record.get("BMI", ""),
                    "diseases": record.get("Diseases", ""),
                    "risk_level": record.get("Risk_Level", ""),
                    "timestamp": record.get("Timestamp", ""),
                }
        return None
    except Exception:
        logger.exception("Error loading risk profile user_id=%s", user_id)
        return None


def _build_briefing_safe(user_id: str, issue_type: str, description: str) -> str:
    """ห่อ ``build_pre_consult_briefing`` ไม่ให้โยน exception ขึ้นมาทำลาย packet."""
    try:
        from services.presession import build_pre_consult_briefing
        return build_pre_consult_briefing(user_id, issue_type, description) or ""
    except Exception:
        logger.exception("Error building pre-consult briefing user_id=%s", user_id)
        return ""


def get_preconsult_packet(
    queue_id: str,
    *,
    force_refresh: bool = False,
) -> Optional[dict[str, Any]]:
    """
    รวบรวม context ของผู้ป่วยใน queue 1 รายการเป็น packet ให้พยาบาล "ดูสรุป"
    ก่อนรับเคส teleconsult.

    Packet ประกอบด้วย:
    - **Queue context**: queue_id, issue_type/label, priority, queued_at, รอมา
    - **Patient identifier**: user_id (เต็มและย่อ)
    - **Description**: ที่ผู้ป่วยพิมพ์ตอน ContactNurse (truncate 500 chars)
    - **Recent timeline**: 5 events ล่าสุด (symptom + session) จาก ``get_patient_timeline``
    - **Latest risk profile**: age/sex/BMI/diseases/risk_level
    - **Pending reminders**: 5 รายการที่ยัง pending
    - **Briefing**: rule-based summary + 2-3 คำถามที่ควรถาม (LLM ถ้าเปิด)

    คืน ``None`` ถ้าหา queue row ไม่เจอ (queue ถูกรับแล้วหรือ id ผิด).
    """
    if not queue_id:
        return None

    cache_key = f"{CACHE_KEY_PRECONSULT_PREFIX}:{queue_id}"
    if not force_refresh:
        cached = ttl_cache.get(cache_key)
        if cached is not None:
            incr("dashboard.cache_hit.preconsult")
            return cached

    incr("dashboard.cache_miss.preconsult")

    queue_row = _find_queue_row(queue_id)
    if not queue_row:
        return None

    user_id = queue_row.get("user_id") or ""
    session_id = queue_row.get("session_id") or ""
    issue_type = queue_row.get("issue_type") or ""

    description = _load_session_description(session_id)
    timeline = get_patient_timeline(user_id, days=14) if user_id else _empty_timeline("")
    pending = _load_pending_reminders_safe(user_id)
    risk_profile = _load_latest_risk_profile(user_id)
    briefing = _build_briefing_safe(user_id, issue_type, description)

    packet = {
        "queue_id": queue_id,
        "session_id": session_id,
        "user_id": user_id,
        "user_id_short": _short_user_id(user_id),
        "issue_type": issue_type,
        "issue_label": _issue_label(issue_type),
        "priority": queue_row.get("priority", 3),
        "priority_label": queue_row.get("priority_label", ""),
        "queued_at": queue_row.get("queued_at_full", ""),
        "waited_minutes": queue_row.get("waited_minutes", 0),
        "description": (description or "")[:500],
        "latest_risk_level": timeline.get("latest_risk_level", ""),
        "symptom_count": timeline.get("symptom_count", 0),
        "session_count": timeline.get("session_count", 0),
        "recent_events": (timeline.get("events") or [])[:5],
        "pending_reminders": pending,
        "risk_profile": risk_profile,
        "briefing": briefing,
    }

    ttl_cache.set(cache_key, packet, TTL_PRECONSULT_SECONDS)
    return packet
