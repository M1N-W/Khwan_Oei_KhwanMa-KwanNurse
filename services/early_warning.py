# -*- coding: utf-8 -*-
"""
Early Warning Service (Phase 2-D)

Detects deteriorating post-operative patients by looking at trends across
their recent symptom reports.

Two entry points:

1. `check_user_early_warning(user_id)` — called right after a new symptom
   report is saved. Pulls the user's recent reports and alerts the nurse
   group if any trend rule fires. Best-effort: never raises.

2. `run_early_warning_scan()` — scheduled daily. Iterates over all users
   who reported in the last look-back window and runs the same checks.
   Emits one alert per user per day (dedup is tracked in-memory per
   process; good enough for a single-worker deployment).

Trend rules (all rule-based so the service works without an LLM key):

- **rising_risk**: last 3 reports' risk_score strictly non-decreasing AND
  latest score >= previous + 2. Catches gradual deterioration.
- **persistent_fever**: fever detected on >=2 reports in the last 3 days.
- **worsening_wound**: wound severity ordinal increased in the last 3
  reports (normal < inflamed < pus).
- **silence_after_high_risk**: most recent report was high risk AND was
  >= 2 days ago with no newer report. Patient may have stopped reporting.
- **repeated_high_risk**: >=2 high-risk reports in the last 5 days.
"""
from datetime import datetime, timedelta

from config import LOCAL_TZ, NURSE_GROUP_ID, get_logger
from database import get_recent_symptom_reports
from services.notification import send_line_push
from utils.pii import scrub_user_id

logger = get_logger(__name__)


# Process-local dedup: user_id -> date string of last alert. Resets on
# worker restart; a stronger store (Redis / Sheet) is out-of-scope for P2-D.
_last_alert_by_user = {}


# ---------------------------------------------------------------------------
# Text helpers — kept intentionally simple so the scan is predictable.
# ---------------------------------------------------------------------------
_FEVER_POSITIVE = ("มี", "ตัวร้อน", "fever", "hot", "ไข้", "ร้อน")
_FEVER_NEGATIVE = ("ไม่มี", "ไม่ไข้", "ไม่มีไข้", "ไม่ร้อน", "ปกติ", "normal", "no fever")


def _has_fever(fever_text):
    t = str(fever_text or "").strip().lower()
    if not t or t in ("ไม่", "no"):
        return False
    if any(neg in t for neg in _FEVER_NEGATIVE):
        return False
    return any(p in t for p in _FEVER_POSITIVE)


def _wound_severity(wound_text):
    """Ordinal severity: 0=unknown/normal, 1=inflamed, 2=pus/discharge."""
    t = str(wound_text or "").lower()
    if any(x in t for x in ("หนอง", "pus", "discharge", "มีกลิ่น", "แฉะ")):
        return 2
    if any(x in t for x in ("บวมแดง", "อักเสบ", "swelling", "inflamed", "red")):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------
def analyze_symptom_trend(reports):
    """
    Run rule-based trend detection over a list of reports.

    Args:
        reports: newest-first list[dict] as produced by
                 database.get_recent_symptom_reports.

    Returns:
        dict with keys:
            triggered (bool):  True if any rule fired
            flags (list[str]): trigger names in the order detected
            details (list[str]): human-readable Thai descriptions
            max_score (int):   highest risk_score in the window
    """
    result = {
        "triggered": False,
        "flags": [],
        "details": [],
        "max_score": 0,
    }
    if not reports:
        return result

    scores = [r.get("risk_score") or 0 for r in reports]
    result["max_score"] = max(scores) if scores else 0

    now = datetime.now(tz=LOCAL_TZ)

    # --- rising_risk: score trend across the last 3 reports (oldest->newest)
    if len(reports) >= 3:
        last3 = list(reversed(reports[:3]))  # oldest first
        s = [r.get("risk_score") or 0 for r in last3]
        if s[0] <= s[1] <= s[2] and s[2] >= s[0] + 2:
            result["flags"].append("rising_risk")
            result["details"].append(
                f"คะแนนความเสี่ยงเพิ่มขึ้นต่อเนื่อง: {s[0]} → {s[1]} → {s[2]}"
            )

    # --- persistent_fever: >=2 fever-positive reports in last 3 days
    three_days_ago = now - timedelta(days=3)
    fever_recent = [
        r for r in reports
        if r.get("timestamp")
        and r["timestamp"] >= three_days_ago
        and _has_fever(r.get("fever"))
    ]
    if len(fever_recent) >= 2:
        result["flags"].append("persistent_fever")
        result["details"].append(
            f"มีไข้ต่อเนื่อง {len(fever_recent)} ครั้งใน 3 วัน"
        )

    # --- worsening_wound: wound severity increased across last 3 reports
    if len(reports) >= 2:
        last_n = list(reversed(reports[: min(3, len(reports))]))
        sev = [_wound_severity(r.get("wound")) for r in last_n]
        if len(sev) >= 2 and sev[-1] > sev[0] and sev[-1] >= 1:
            result["flags"].append("worsening_wound")
            label = {1: "บวมแดง", 2: "มีหนอง/แฉะ"}[sev[-1]]
            result["details"].append(f"แผลแย่ลงจนถึง: {label}")

    # --- silence_after_high_risk
    latest = reports[0]
    latest_ts = latest.get("timestamp")
    if latest_ts and (latest.get("risk_score") or 0) >= 3:
        if now - latest_ts >= timedelta(days=2):
            result["flags"].append("silence_after_high_risk")
            hours = int((now - latest_ts).total_seconds() // 3600)
            result["details"].append(
                f"ไม่ได้รายงานอาการเพิ่มเติม {hours} ชม. หลังรายงานเสี่ยงสูง"
            )

    # --- repeated_high_risk: >=2 reports with score >=3 in 5 days
    five_days_ago = now - timedelta(days=5)
    high_recent = [
        r for r in reports
        if r.get("timestamp")
        and r["timestamp"] >= five_days_ago
        and (r.get("risk_score") or 0) >= 3
    ]
    if len(high_recent) >= 2:
        result["flags"].append("repeated_high_risk")
        result["details"].append(
            f"มีรายงานเสี่ยงสูง {len(high_recent)} ครั้งใน 5 วัน"
        )

    result["triggered"] = bool(result["flags"])
    return result


def _format_alert(user_id, analysis, reports):
    """Build the Thai alert message for the nurse group."""
    lines = [
        "⚠️ Early-Warning: ตรวจพบแนวโน้มน่ากังวล",
        f"👤 ผู้ป่วย: {user_id}",
        f"🔎 Flags: {', '.join(analysis['flags'])}",
        f"📈 คะแนนสูงสุดในช่วง: {analysis['max_score']}",
        "รายละเอียด:",
    ]
    for d in analysis["details"]:
        lines.append(f"  • {d}")
    if reports:
        latest = reports[0]
        ts = latest.get("timestamp")
        ts_str = ts.strftime("%d/%m %H:%M") if ts else "-"
        lines.append(f"🕐 รายงานล่าสุด: {ts_str} ({latest.get('risk_level', '-')})")
    lines.append("แนะนำ: ติดต่อผู้ป่วยเพื่อประเมินซ้ำ")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def check_user_early_warning(user_id, lookback_days=7, notify=True):
    """
    Evaluate a single user's recent reports and push an alert if needed.

    Returns the analysis dict so callers/tests can inspect it. Never raises.
    """
    try:
        if not user_id:
            return None
        reports = get_recent_symptom_reports(user_id=user_id, days=lookback_days, limit=20)
        analysis = analyze_symptom_trend(reports)

        if analysis["triggered"] and notify:
            # dedup: one alert per user per day
            today = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d")
            if _last_alert_by_user.get(user_id) == today:
                logger.debug("Early-warning already sent today for %s",
                             scrub_user_id(user_id))
                try:
                    from services.metrics import incr as _metric
                    _metric("early_warning.dedup_skip")
                except Exception:
                    pass
            elif NURSE_GROUP_ID:
                msg = _format_alert(user_id, analysis, reports)
                send_line_push(msg, NURSE_GROUP_ID)
                _last_alert_by_user[user_id] = today
                logger.info(
                    "Early-warning alert sent for %s: flags=%s",
                    scrub_user_id(user_id), analysis["flags"],
                )
                try:
                    from services.metrics import incr as _metric
                    _metric("early_warning.alert_sent")
                except Exception:
                    pass
            else:
                logger.warning(
                    "Early-warning would fire for %s but NURSE_GROUP_ID is unset",
                    scrub_user_id(user_id),
                )
                try:
                    from services.metrics import incr as _metric
                    _metric("early_warning.nurse_group_missing")
                except Exception:
                    pass
        return analysis
    except Exception:
        logger.exception("Error in check_user_early_warning")
        return None


def run_early_warning_scan(lookback_days=7):
    """
    Scan all users who reported in the last `lookback_days` and alert on
    any triggered trend. Intended to be scheduled once per day.

    Returns: int — number of users flagged (useful for logging).
    """
    try:
        logger.info("Early-warning scan started (lookback=%d days)", lookback_days)
        all_reports = get_recent_symptom_reports(user_id=None, days=lookback_days, limit=500)
        if not all_reports:
            logger.info("Early-warning scan: no reports in window")
            return 0

        # Group by user
        by_user = {}
        for r in all_reports:
            uid = r.get("user_id") or ""
            if not uid:
                continue
            by_user.setdefault(uid, []).append(r)

        flagged = 0
        for uid, reports in by_user.items():
            # Already newest-first because source is sorted; keep as-is.
            analysis = analyze_symptom_trend(reports)
            if analysis["triggered"]:
                flagged += 1
                today = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d")
                if _last_alert_by_user.get(uid) == today:
                    continue
                if NURSE_GROUP_ID:
                    try:
                        send_line_push(_format_alert(uid, analysis, reports), NURSE_GROUP_ID)
                        _last_alert_by_user[uid] = today
                    except Exception:
                        logger.exception("Failed to push early-warning alert")

        logger.info("Early-warning scan complete: flagged=%d users", flagged)
        return flagged
    except Exception:
        logger.exception("Error in run_early_warning_scan")
        return 0


def _reset_dedup_for_tests():
    _last_alert_by_user.clear()
