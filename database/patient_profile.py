# -*- coding: utf-8 -*-
"""
PatientProfile sheet (Sprint 2 S2-3).

Persists *sticky* patient demographics that the bot needs across sessions
to personalize content (mainly knowledge recommendations). Kept in a
dedicated sheet rather than expanding ``RiskProfile`` so the change is
purely additive — no migration of existing rows is required.

Schema (Google Sheets ``PatientProfile``):

    User_ID | Age | Sex | Surgery_Type | Surgery_Date | Diseases | Updated_At

Notes
-----
- One row per ``User_ID`` (upsert semantics — ``upsert_patient_profile``
  rewrites the existing row if found, else appends).
- Empty cells mean "unknown"; callers should treat them as missing rather
  than as concrete values.
- Does NOT replace ``RiskProfile`` — that sheet still owns the time-series
  of every risk assessment. ``PatientProfile`` only holds the latest known
  demographics convenient for personalization.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from config import LOCAL_TZ, SHEET_PATIENT_PROFILE, get_logger
from database.sheets import column_number_to_letter, get_worksheet

logger = get_logger(__name__)


# Order must stay stable — readers use index-by-name but writers (append /
# update_cells) rely on this canonical order.
HEADERS = [
    "User_ID", "Age", "Sex", "Surgery_Type", "Surgery_Date", "Diseases",
    "Updated_At", "First_Name", "Last_Name", "HN",
]


def _now_str() -> str:
    return datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(headers: list[str], row: list[str]) -> dict[str, Any]:
    """Pad short rows + zip with header names, then coerce known fields."""
    padded = list(row) + [""] * max(0, len(headers) - len(row))
    rec = dict(zip(headers, padded))

    age_raw = (rec.get("Age") or "").strip()
    try:
        age = int(age_raw) if age_raw else None
    except (TypeError, ValueError):
        age = None

    diseases_raw = (rec.get("Diseases") or "").strip()
    diseases = [d.strip() for d in diseases_raw.split(",") if d.strip()] if diseases_raw else []

    first_name = (rec.get("First_Name") or "").strip()
    last_name = (rec.get("Last_Name") or "").strip()
    hn = (rec.get("HN") or "").strip()
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    display_label = display_name or hn
    if display_name and hn:
        display_label = f"{display_name} · HN {hn}"

    return {
        "user_id": rec.get("User_ID", ""),
        "age": age,
        "sex": (rec.get("Sex") or "").strip().lower() or None,
        "surgery_type": (rec.get("Surgery_Type") or "").strip().lower() or None,
        "surgery_date": (rec.get("Surgery_Date") or "").strip() or None,
        "diseases": diseases,
        "updated_at": (rec.get("Updated_At") or "").strip() or None,
        "first_name": first_name or None,
        "last_name": last_name or None,
        "hn": hn or None,
        "display_name": display_name or None,
        "display_label": display_label or None,
    }


def _profile_to_row(user_id: str, profile: dict[str, Any]) -> list[str]:
    """Encode a profile dict into the canonical row shape."""
    age = profile.get("age")
    age_str = str(age) if age not in (None, "") else ""

    diseases = profile.get("diseases") or []
    if isinstance(diseases, str):
        diseases_str = diseases
    else:
        diseases_str = ", ".join(str(d).strip() for d in diseases if str(d).strip())

    return [
        user_id or "",
        age_str,
        (profile.get("sex") or "").strip().lower(),
        (profile.get("surgery_type") or "").strip().lower(),
        (profile.get("surgery_date") or "").strip(),
        diseases_str,
        _now_str(),
        (profile.get("first_name") or "").strip()[:80],
        (profile.get("last_name") or "").strip()[:80],
        (profile.get("hn") or "").strip()[:40],
    ]


def read_patient_profile(user_id: str) -> Optional[dict[str, Any]]:
    """
    Look up the stored profile row for ``user_id``.

    Returns:
        dict with normalized fields if a row exists, else ``None``.
        Never raises — sheet errors return ``None`` and are logged.
    """
    if not user_id:
        return None
    try:
        sheet = get_worksheet(SHEET_PATIENT_PROFILE)
        if not sheet:
            return None
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return None

        headers = values[0]
        idx_uid = headers.index("User_ID") if "User_ID" in headers else 0
        # Scan from bottom — most recently written row wins if duplicates exist.
        for row in reversed(values[1:]):
            if len(row) > idx_uid and row[idx_uid] == user_id:
                return _row_to_dict(headers, row)
        return None
    except Exception:
        logger.exception("read_patient_profile failed user_id=%s", user_id)
        return None


def upsert_patient_profile(user_id: str, profile: dict[str, Any]) -> bool:
    """
    Insert or update the profile row for ``user_id``.

    Behavior:
    - If a row with this ``user_id`` already exists, the entire row is
      overwritten with the latest profile (we always rewrite the full row
      rather than diff cells — simpler, single API call, fine for our
      throughput).
    - Otherwise, a new row is appended.

    Args:
        user_id: LINE user id.
        profile: dict with any subset of {age, sex, surgery_type,
            surgery_date, diseases}.

    Returns:
        True on success, False on any error (does not raise).
    """
    if not user_id:
        return False
    try:
        sheet = get_worksheet(SHEET_PATIENT_PROFILE)
        if not sheet:
            logger.error("upsert_patient_profile: sheet handle unavailable")
            return False

        new_row = _profile_to_row(user_id, profile or {})

        values = sheet.get_all_values()
        # Empty sheet — write headers first then append the row.
        if not values:
            sheet.append_row(HEADERS, value_input_option="USER_ENTERED")
            sheet.append_row(new_row, value_input_option="USER_ENTERED")
            logger.info("upsert_patient_profile: created sheet with headers + row user=%s", user_id)
            return True

        headers = values[0]
        idx_uid = headers.index("User_ID") if "User_ID" in headers else 0

        # Find existing row (1-indexed sheet row numbers; row 1 = header)
        for sheet_row_index, row in enumerate(values[1:], start=2):
            if len(row) > idx_uid and row[idx_uid] == user_id:
                # Update existing row — write the whole row as a single range.
                end_col_letter = column_number_to_letter(len(HEADERS))
                target_range = f"A{sheet_row_index}:{end_col_letter}{sheet_row_index}"
                sheet.update(target_range, [new_row], value_input_option="USER_ENTERED")
                logger.info("upsert_patient_profile: updated row %d user=%s",
                            sheet_row_index, user_id)
                return True

        # Not found — append.
        sheet.append_row(new_row, value_input_option="USER_ENTERED")
        logger.info("upsert_patient_profile: appended row user=%s", user_id)
        return True

    except Exception:
        logger.exception("upsert_patient_profile failed user_id=%s", user_id)
        return False
