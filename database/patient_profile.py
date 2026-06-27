# -*- coding: utf-8 -*-
"""
PatientProfile sheet.

The sheet is the long-lived, one-row-per-LINE-user profile store. KWN-02 adds
the patient registry contract without requiring a one-time migration: writers
append missing canonical headers and preserve any future/unknown columns.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from config import (
    LOCAL_TZ,
    PATIENT_CONSENT_VERSION,
    SHEET_PATIENT_PROFILE,
    get_logger,
)
from database.sheets import column_number_to_letter, get_worksheet
from utils.parsers import is_valid_thai_mobile, normalize_phone_number
from utils.pii import scrub_user_id
from services.cache import ttl_cache

logger = get_logger(__name__)


HEADERS = [
    "User_ID", "Age", "Sex", "Surgery_Type", "Surgery_Date", "Diseases",
    "Updated_At", "First_Name", "Last_Name", "HN", "Phone",
    "Registration_Status", "Registered_At", "Consent_Version", "Consent_At",
    "Last_Active_At",
]

_FIELD_TO_HEADER = {
    "age": "Age",
    "sex": "Sex",
    "surgery_type": "Surgery_Type",
    "surgery_date": "Surgery_Date",
    "diseases": "Diseases",
    "first_name": "First_Name",
    "last_name": "Last_Name",
    "hn": "HN",
    "phone": "Phone",
    "last_active_at": "Last_Active_At",
}


@dataclass(frozen=True)
class PatientProfileReadResult:
    available: bool
    profile: Optional[dict[str, Any]]


def _now_str() -> str:
    return datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) != 10:
        return ""
    return f"{digits[:2]}X-XXX-{digits[-4:]}"


def _coerce_phone(value: Any) -> str:
    normalized = normalize_phone_number(value)
    if normalized and is_valid_thai_mobile(normalized):
        return normalized
    return ""


def _registration_status(rec: dict[str, Any]) -> str:
    required = (
        (rec.get("First_Name") or "").strip(),
        (rec.get("Last_Name") or "").strip(),
        (rec.get("HN") or "").strip(),
        _coerce_phone(rec.get("Phone")),
        (rec.get("Consent_Version") or "").strip() == PATIENT_CONSENT_VERSION,
        bool((rec.get("Consent_At") or "").strip()),
    )
    return "registered" if all(required) else "incomplete"


def _effective_headers(raw_headers: list[str]) -> tuple[list[str], bool]:
    headers = list(raw_headers or [])
    changed = False
    for header in HEADERS:
        if header not in headers:
            headers.append(header)
            changed = True
    return headers, changed


def _write_header_if_needed(sheet, headers: list[str], changed: bool) -> None:
    if not changed:
        return
    end_col = column_number_to_letter(len(headers))
    sheet.update(f"A1:{end_col}1", [headers], value_input_option="USER_ENTERED")


def _row_record(headers: list[str], row: list[str]) -> dict[str, Any]:
    padded = list(row) + [""] * max(0, len(headers) - len(row))
    return dict(zip(headers, padded))


def _diseases_to_string(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    return ", ".join(str(d).strip() for d in value if str(d).strip())


def _apply_profile_to_record(
    user_id: str,
    profile: dict[str, Any],
    existing_record: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    rec = dict(existing_record or {})
    rec["User_ID"] = user_id or ""
    now = _now_str()

    previous_status = _registration_status(rec)
    profile = profile or {}

    for field, header in _FIELD_TO_HEADER.items():
        if field not in profile:
            continue
        value = profile.get(field)
        if field == "age":
            rec[header] = str(value) if value not in (None, "") else ""
        elif field in {"sex", "surgery_type"}:
            rec[header] = str(value or "").strip().lower()
        elif field == "diseases":
            rec[header] = _diseases_to_string(value)
        elif field == "phone":
            rec[header] = _coerce_phone(value)
        elif field == "last_active_at":
            rec[header] = str(value or "").strip()
        else:
            limit = 40 if field == "hn" else 80
            text = " ".join(str(value or "").strip().split())[:limit]
            rec[header] = text.upper() if field == "hn" else text

    if profile.get("consent_granted") is True:
        rec["Consent_Version"] = PATIENT_CONSENT_VERSION
        if not (rec.get("Consent_At") or "").strip():
            rec["Consent_At"] = now

    rec["Updated_At"] = now
    new_status = _registration_status(rec)
    rec["Registration_Status"] = new_status
    if previous_status != "registered" and new_status == "registered":
        if not (rec.get("Registered_At") or "").strip():
            rec["Registered_At"] = now
    return rec


def _record_to_row(headers: list[str], rec: dict[str, Any]) -> list[str]:
    return [rec.get(header, "") for header in headers]


def _row_to_dict(headers: list[str], row: list[str]) -> dict[str, Any]:
    """Pad short rows + zip with header names, then coerce known fields."""
    rec = _row_record(headers, row)

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
    phone = _coerce_phone(rec.get("Phone"))
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    display_label = display_name or hn
    if display_name and hn:
        display_label = f"{display_name} · HN {hn}"

    status = _registration_status(rec)
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
        "phone": phone or None,
        "masked_phone": _mask_phone(phone),
        "registration_status": status,
        "registered_at": (rec.get("Registered_At") or "").strip() or None,
        "consent_version": (rec.get("Consent_Version") or "").strip() or None,
        "consent_at": (rec.get("Consent_At") or "").strip() or None,
        "consent_granted": (rec.get("Consent_Version") or "").strip() == PATIENT_CONSENT_VERSION and bool((rec.get("Consent_At") or "").strip()),
        "last_active_at": (rec.get("Last_Active_At") or "").strip() or None,
        "display_name": display_name or None,
        "display_label": display_label or None,
    }


def read_patient_profile_result(user_id: str) -> PatientProfileReadResult:
    """Read profile with an explicit storage availability signal."""
    if not user_id:
        return PatientProfileReadResult(True, None)
    
    # Check process-level cache (disabled during unit tests to prevent test pollution)
    import sys
    cache_key = f"db:profile:v1:{user_id}"
    is_testing = "unittest" in sys.modules
    
    if not is_testing:
        cached = ttl_cache.get(cache_key)
        if cached is not None:
            return PatientProfileReadResult(True, cached)

    try:
        sheet = get_worksheet(SHEET_PATIENT_PROFILE)
        if not sheet:
            return PatientProfileReadResult(False, None)
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return PatientProfileReadResult(True, None)

        headers = values[0]
        idx_uid = headers.index("User_ID") if "User_ID" in headers else 0
        for row in reversed(values[1:]):
            if len(row) > idx_uid and row[idx_uid] == user_id:
                profile_dict = _row_to_dict(headers, row)
                if not is_testing:
                    ttl_cache.set(cache_key, profile_dict, ttl_seconds=30)
                return PatientProfileReadResult(True, profile_dict)
        return PatientProfileReadResult(True, None)
    except Exception:
        logger.exception("read_patient_profile failed user_id=%s", scrub_user_id(user_id))
        return PatientProfileReadResult(False, None)


def read_patient_profile(user_id: str) -> Optional[dict[str, Any]]:
    """Compatibility wrapper: return profile dict or None."""
    return read_patient_profile_result(user_id).profile


def upsert_patient_profile(user_id: str, profile: dict[str, Any]) -> bool:
    """Insert or update one profile row, migrating headers additively."""
    if not user_id:
        return False
    
    # Invalidate cache before/during write to avoid stale reads
    cache_key = f"db:profile:v1:{user_id}"
    ttl_cache.invalidate(cache_key)

    try:
        sheet = get_worksheet(SHEET_PATIENT_PROFILE)
        if not sheet:
            logger.error("upsert_patient_profile: sheet handle unavailable")
            return False

        values = sheet.get_all_values()
        if not values:
            rec = _apply_profile_to_record(user_id, profile or {})
            sheet.append_row(HEADERS, value_input_option="USER_ENTERED")
            sheet.append_row(_record_to_row(HEADERS, rec), value_input_option="USER_ENTERED")
            logger.info(
                "upsert_patient_profile: created sheet with headers + row user=%s",
                scrub_user_id(user_id),
            )
            return True

        headers, header_changed = _effective_headers(values[0])
        _write_header_if_needed(sheet, headers, header_changed)
        idx_uid = headers.index("User_ID") if "User_ID" in headers else 0

        for sheet_row_index, row in enumerate(values[1:], start=2):
            if len(row) > idx_uid and row[idx_uid] == user_id:
                existing = _row_record(headers, row)
                rec = _apply_profile_to_record(user_id, profile or {}, existing)
                new_row = _record_to_row(headers, rec)
                end_col = column_number_to_letter(len(headers))
                target_range = f"A{sheet_row_index}:{end_col}{sheet_row_index}"
                sheet.update(target_range, [new_row], value_input_option="USER_ENTERED")
                logger.info(
                    "upsert_patient_profile: updated row %d user=%s",
                    sheet_row_index,
                    scrub_user_id(user_id),
                )
                return True

        rec = _apply_profile_to_record(user_id, profile or {})
        sheet.append_row(_record_to_row(headers, rec), value_input_option="USER_ENTERED")
        logger.info("upsert_patient_profile: appended row user=%s", scrub_user_id(user_id))
        return True

    except Exception:
        logger.exception("upsert_patient_profile failed user_id=%s", scrub_user_id(user_id))
        return False
