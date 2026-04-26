# -*- coding: utf-8 -*-
"""
WoundAnalysisLog persistence (Sprint 2 S2-2).

Schema (Google Sheets ``WoundAnalysisLog``):

    Timestamp | User_ID | Severity | Observations | Advice | Confidence | Image_Size_KB | Message_ID

- ``Observations`` is stored as a single semi-colon-separated string so it
  fits in one column. Use ``parse_observations`` if a reader needs to split.
- ``Image_Size_KB`` is integer KB (no decimal) — useful for spot-checking
  outliers without storing the actual image.
- ``Message_ID`` is the LINE message id; useful for re-downloading the image
  from LINE Content API for a few days (LINE retains ~1 week).

Privacy:
- We never store the image bytes themselves anywhere. Only metadata.
- ``User_ID`` is the LINE user ID — same convention as other sheets.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from config import LOCAL_TZ, SHEET_WOUND_ANALYSIS_LOG, get_logger
from database.sheets import get_worksheet

logger = get_logger(__name__)


_OBSERVATIONS_SEPARATOR = " ; "


def _join_observations(observations: list[str]) -> str:
    """Encode list of short strings into a single cell value."""
    if not observations:
        return ""
    return _OBSERVATIONS_SEPARATOR.join(o.strip() for o in observations if o and str(o).strip())


def parse_observations(cell_value: str) -> list[str]:
    """Decode a stored observations cell back into a list."""
    if not cell_value:
        return []
    return [part.strip() for part in cell_value.split(_OBSERVATIONS_SEPARATOR) if part.strip()]


def save_wound_analysis(
    user_id: str,
    severity: str,
    observations: list[str],
    advice: str,
    confidence: float,
    image_size_kb: int,
    message_id: str = "",
) -> bool:
    """
    Append a wound analysis result row to the WoundAnalysisLog sheet.

    Args:
        user_id: LINE user ID.
        severity: ``low`` / ``medium`` / ``high`` (already validated upstream).
        observations: list of short Thai observation strings.
        advice: short Thai advice string.
        confidence: 0.0-1.0.
        image_size_kb: integer KB of original image (for billing audit).
        message_id: LINE message id (optional, for re-fetching image).

    Returns:
        bool: True if row was appended, False on any error (caller should
        still send the user the analysis result and the nurse alert — losing
        the audit row is not a user-facing failure).
    """
    try:
        sheet = get_worksheet(SHEET_WOUND_ANALYSIS_LOG)
        if not sheet:
            logger.error("wound_logs: sheet client/handle unavailable")
            return False

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id or "",
            severity or "",
            _join_observations(observations or []),
            (advice or "")[:500],
            f"{float(confidence):.2f}",
            str(int(image_size_kb)) if image_size_kb else "0",
            message_id or "",
        ]

        from database.retry import retry_sheet_op
        retry_sheet_op(
            lambda: sheet.append_row(row, value_input_option="USER_ENTERED"),
            op_name="wound_logs.append",
        )
        logger.info(
            "wound_logs: appended row user=%s severity=%s confidence=%.2f kb=%d",
            user_id, severity, float(confidence), int(image_size_kb),
        )
        return True

    except Exception:
        logger.exception("wound_logs: failed to append row user_id=%s", user_id)
        return False


def get_recent_wound_analyses(
    user_id: Optional[str] = None,
    days: int = 14,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Read recent wound analyses from the sheet, newest first.

    Args:
        user_id: If provided, filter to a single user; else return all users.
        days: Look-back window in days (default 14).
        limit: Max rows to return.

    Returns:
        list[dict] with keys ``timestamp``, ``user_id``, ``severity``,
        ``observations`` (list), ``advice``, ``confidence`` (float),
        ``image_size_kb`` (int), ``message_id``.
        Empty list on any error or empty sheet.
    """
    try:
        sheet = get_worksheet(SHEET_WOUND_ANALYSIS_LOG)
        if not sheet:
            return []
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return []

        headers = values[0]

        def col(name: str, default: int) -> int:
            return headers.index(name) if name in headers else default

        idx_ts = col("Timestamp", 0)
        idx_uid = col("User_ID", 1)
        idx_sev = col("Severity", 2)
        idx_obs = col("Observations", 3)
        idx_adv = col("Advice", 4)
        idx_conf = col("Confidence", 5)
        idx_size = col("Image_Size_KB", 6)
        idx_msg = col("Message_ID", 7)

        cutoff = datetime.now(tz=LOCAL_TZ).timestamp() - days * 86400
        out: list[dict[str, Any]] = []

        for row in reversed(values[1:]):
            if len(row) <= idx_uid:
                continue
            if user_id and row[idx_uid] != user_id:
                continue

            ts_raw = row[idx_ts] if len(row) > idx_ts else ""
            try:
                ts = datetime.strptime(ts_raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
            except (ValueError, TypeError):
                continue
            if ts.timestamp() < cutoff:
                continue

            try:
                confidence = float(row[idx_conf]) if len(row) > idx_conf else 0.0
            except (ValueError, TypeError):
                confidence = 0.0
            try:
                size_kb = int(row[idx_size]) if len(row) > idx_size else 0
            except (ValueError, TypeError):
                size_kb = 0

            out.append({
                "timestamp": ts,
                "user_id": row[idx_uid],
                "severity": row[idx_sev] if len(row) > idx_sev else "",
                "observations": parse_observations(row[idx_obs] if len(row) > idx_obs else ""),
                "advice": row[idx_adv] if len(row) > idx_adv else "",
                "confidence": confidence,
                "image_size_kb": size_kb,
                "message_id": row[idx_msg] if len(row) > idx_msg else "",
            })
            if len(out) >= limit:
                break

        return out

    except Exception:
        logger.exception("wound_logs: failed to read recent rows user_id=%s", user_id)
        return []
