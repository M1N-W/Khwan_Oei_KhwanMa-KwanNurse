# -*- coding: utf-8 -*-
"""Persistent backlog for failed high-risk nurse alerts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from config import LOCAL_TZ, SHEET_FAILED_NURSE_ALERTS, get_logger
from database.sheets import get_spreadsheet, get_worksheet

logger = get_logger(__name__)

HEADER = [
    "Created_At",
    "Idempotency_Key",
    "Event_Type",
    "User_ID",
    "Risk_Level",
    "Risk_Score",
    "Payload_JSON",
    "Notification_Message",
    "Status",
    "Retry_Count",
    "Last_Error",
]

_EVENT_TYPE = "symptom_assessment"
_STATUS_PENDING = "pending"
_LAST_ERROR = "initial_line_push_failed"
USER_ID_MAX_CHARS = 255
RISK_CODE_MAX_CHARS = 32
PAIN_MAX_CHARS = 64
WOUND_MAX_CHARS = 500
FEVER_MAX_CHARS = 500
MOBILITY_MAX_CHARS = 500
NEURO_MAX_CHARS = 500
NOTIFICATION_MESSAGE_MAX_CHARS = 4000
PAYLOAD_JSON_MAX_CHARS = 4000


def _bound(value: str, limit: int) -> str:
    return value[:limit]


def _normalize_identifier(value: Any, limit: int = USER_ID_MAX_CHARS) -> str:
    """Normalize an opaque identifier without changing its case."""
    raw = "" if value is None else str(value).strip()
    return _bound(raw, limit)


def _normalize_free_text(value: Any, limit: int) -> str:
    return "" if value is None else str(value).strip().casefold()


def _normalized_payload(
    user_id: Any,
    risk_code: Any,
    risk_score: Any,
    pain: Any,
    wound: Any,
    fever: Any,
    mobility: Any,
    neuro: Any = None,
) -> dict[str, Any]:
    try:
        score = int(risk_score)
    except (TypeError, ValueError):
        score = 0
    return {
        "user_id": _normalize_identifier(user_id),
        "risk_code": _bound(_normalize_free_text(risk_code, RISK_CODE_MAX_CHARS), RISK_CODE_MAX_CHARS),
        "risk_score": score,
        "pain": _bound(_normalize_free_text(pain, PAIN_MAX_CHARS), PAIN_MAX_CHARS),
        "wound": _bound(_normalize_free_text(wound, WOUND_MAX_CHARS), WOUND_MAX_CHARS),
        "fever": _bound(_normalize_free_text(fever, FEVER_MAX_CHARS), FEVER_MAX_CHARS),
        "mobility": _bound(_normalize_free_text(mobility, MOBILITY_MAX_CHARS), MOBILITY_MAX_CHARS),
        "neuro": _bound(_normalize_free_text(neuro, NEURO_MAX_CHARS), NEURO_MAX_CHARS),
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _idempotency_key_from_payload(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"symptom-alert:v1:{digest}"


def build_symptom_alert_idempotency_key(
    user_id: Any,
    risk_code: Any,
    risk_score: Any,
    pain: Any,
    wound: Any,
    fever: Any,
    mobility: Any,
    neuro: Any = None,
) -> str:
    """Return an opaque deterministic key for a failed symptom alert."""
    payload = _normalized_payload(
        user_id, risk_code, risk_score, pain, wound, fever, mobility, neuro,
    )
    return _idempotency_key_from_payload(payload)


def _safe_cell(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _get_or_create_sheet():
    sheet = get_worksheet(SHEET_FAILED_NURSE_ALERTS)
    if sheet is not None:
        return sheet

    spreadsheet = get_spreadsheet()
    if spreadsheet is None:
        logger.warning("failed_nurse_alerts: no spreadsheet handle")
        return None

    try:
        sheet = spreadsheet.add_worksheet(
            title=SHEET_FAILED_NURSE_ALERTS,
            rows=1000,
            cols=len(HEADER),
        )
        sheet.append_row(HEADER, value_input_option="USER_ENTERED")
        logger.info("failed_nurse_alerts: auto-created sheet")
        return sheet
    except Exception:
        logger.exception("failed_nurse_alerts: auto-create failed")
        return None


def save_failed_symptom_alert(
    *,
    user_id: Any,
    risk_code: Any,
    risk_score: Any,
    pain: Any,
    wound: Any,
    fever: Any,
    mobility: Any,
    neuro: Any = None,
    notification_message: str = "",
) -> bool:
    """
    Append one failed high-risk symptom alert row.

    This does not scan for duplicates and does not retry; the idempotency key is
    for a future worker contract, while the webhook path remains bounded.
    """
    try:
        sheet = _get_or_create_sheet()
        if sheet is None:
            return False

        payload = _normalized_payload(
            user_id, risk_code, risk_score, pain, wound, fever, mobility, neuro,
        )
        payload_json = _canonical_json(payload)
        if len(payload_json) > PAYLOAD_JSON_MAX_CHARS:
            logger.error("failed_nurse_alerts: bounded payload still too large")
            return False
        key = _idempotency_key_from_payload(payload)
        created_at = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            created_at,
            key,
            _EVENT_TYPE,
            payload["user_id"],
            payload["risk_code"],
            payload["risk_score"],
            payload_json,
            _safe_cell(notification_message, NOTIFICATION_MESSAGE_MAX_CHARS),
            _STATUS_PENDING,
            0,
            _LAST_ERROR,
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(
            "failed_nurse_alerts: appended event=%s risk=%s score=%s key=%s",
            _EVENT_TYPE, payload["risk_code"], payload["risk_score"], key,
        )
        return True
    except Exception:
        logger.exception("failed_nurse_alerts: append failed")
        return False
