# -*- coding: utf-8 -*-
"""
Google Sheets Database Module
Handles all interactions with Google Sheets
"""
import base64
import hashlib
import gspread
import json
import os
import time
from datetime import datetime, timedelta
from config import (
    get_logger,
    LOCAL_TZ,
    GSPREAD_CREDENTIALS,
    SPREADSHEET_NAME,
    SHEET_SYMPTOM_LOG,
    SHEET_RISK_PROFILE,
    SHEET_APPOINTMENTS
)

logger = get_logger(__name__)

# Thread-local storage for thread-safe Google Sheets clients
import threading

_thread_local = threading.local()
_CLIENT_TTL_SECONDS = 3000  # 50 minutes
_IDEMPOTENCY_LOCK = threading.RLock()


def _get_local_cache():
    if not hasattr(_thread_local, "sheet_client"):
        _thread_local.sheet_client = None
    if not hasattr(_thread_local, "client_created_at"):
        _thread_local.client_created_at = None
    if not hasattr(_thread_local, "spreadsheet"):
        _thread_local.spreadsheet = None
    if not hasattr(_thread_local, "spreadsheet_created_at"):
        _thread_local.spreadsheet_created_at = None
    if not hasattr(_thread_local, "worksheet_cache"):
        _thread_local.worksheet_cache = {}
    return _thread_local


def _reset_sheet_cache():
    """Reset cached spreadsheet and worksheet handles when the client refreshes."""
    cache = _get_local_cache()
    cache.spreadsheet = None
    cache.spreadsheet_created_at = None
    cache.worksheet_cache = {}


def invalidate_sheet_client():
    """Force reset the thread-local client cache to recover from SSL/connection errors."""
    cache = _get_local_cache()
    cache.sheet_client = None
    cache.client_created_at = None
    cache.spreadsheet = None
    cache.spreadsheet_created_at = None
    cache.worksheet_cache.clear()
    logger.info("Thread-local Google Sheets client cache invalidated.")


def get_sheet_client():
    """
    Get Google Sheets client (singleton with TTL refresh)
    Refreshes the client before the 1-hour OAuth token expiry.
    Returns: gspread client or None
    """
    cache = _get_local_cache()
    now = time.monotonic()
    if (cache.sheet_client is not None and
            cache.client_created_at is not None and
            (now - cache.client_created_at) < _CLIENT_TTL_SECONDS):
        return cache.sheet_client

    # Invalidate stale client
    cache.sheet_client = None
    cache.client_created_at = None
    _reset_sheet_cache()

    try:
        creds_env = GSPREAD_CREDENTIALS or os.environ.get("GOOGLE_CREDS_B64")
        if creds_env:
            if os.environ.get("GOOGLE_CREDS_B64") and not GSPREAD_CREDENTIALS:
                creds_env = base64.b64decode(creds_env).decode("utf-8")
            creds_json = json.loads(creds_env)
            if hasattr(gspread, "service_account_from_dict"):
                cache.sheet_client = gspread.service_account_from_dict(creds_json)
                cache.client_created_at = now
                logger.info("Google Sheets client initialized from environment")
                return cache.sheet_client

        if os.path.exists("credentials.json"):
            cache.sheet_client = gspread.service_account(filename="credentials.json")
            cache.client_created_at = now
            logger.info("Google Sheets client initialized from file")
            return cache.sheet_client

        logger.warning("No Google credentials found")
    except Exception:
        logger.exception("Error connecting to Google Sheets")

    return None


def get_spreadsheet():
    """
    Get the target spreadsheet using the shared client/cache lifecycle.
    """
    cache = _get_local_cache()
    now = time.monotonic()
    if (cache.spreadsheet is not None and
            cache.spreadsheet_created_at is not None and
            (now - cache.spreadsheet_created_at) < _CLIENT_TTL_SECONDS):
        return cache.spreadsheet

    client = get_sheet_client()
    if not client:
        return None

    try:
        cache.spreadsheet = client.open(SPREADSHEET_NAME)
        cache.spreadsheet_created_at = now
        cache.worksheet_cache.clear()
        return cache.spreadsheet
    except Exception:
        logger.exception("Error opening Google Spreadsheet: %s", SPREADSHEET_NAME)
        _reset_sheet_cache()
        return None


_SHEET_VALUES_CACHE = {}


def _patch_worksheet_read_methods(worksheet):
    import sys
    is_testing = "unittest" in sys.modules
    if is_testing:
        return

    title = worksheet.title
    orig_get_all_values = worksheet.get_all_values

    def get_all_values_cached(*args, **kwargs):
        if args or kwargs:
            return orig_get_all_values(*args, **kwargs)
        now = time.monotonic()
        cached = _SHEET_VALUES_CACHE.get(title)
        if cached:
            val, expiry = cached
            if now < expiry:
                return val
        val = orig_get_all_values()
        _SHEET_VALUES_CACHE[title] = (val, now + 10.0)
        return val

    worksheet.get_all_values = get_all_values_cached

    # Patch write methods to invalidate cache on write
    orig_append_row = worksheet.append_row
    def append_row_cached(*args, **kwargs):
        _SHEET_VALUES_CACHE.pop(title, None)
        return orig_append_row(*args, **kwargs)
    worksheet.append_row = append_row_cached

    orig_update = worksheet.update
    def update_cached(*args, **kwargs):
        _SHEET_VALUES_CACHE.pop(title, None)
        return orig_update(*args, **kwargs)
    worksheet.update = update_cached

    orig_delete_rows = worksheet.delete_rows
    def delete_rows_cached(*args, **kwargs):
        _SHEET_VALUES_CACHE.pop(title, None)
        return orig_delete_rows(*args, **kwargs)
    worksheet.delete_rows = delete_rows_cached

    orig_batch_update = worksheet.batch_update
    def batch_update_cached(*args, **kwargs):
        _SHEET_VALUES_CACHE.pop(title, None)
        return orig_batch_update(*args, **kwargs)
    worksheet.batch_update = batch_update_cached

    orig_update_cells = worksheet.update_cells
    def update_cells_cached(*args, **kwargs):
        _SHEET_VALUES_CACHE.pop(title, None)
        return orig_update_cells(*args, **kwargs)
    worksheet.update_cells = update_cells_cached


def get_worksheet(sheet_name):
    """
    Get a worksheet handle with the same TTL lifecycle as the sheet client.
    """
    cache = _get_local_cache()
    if sheet_name in cache.worksheet_cache:
        return cache.worksheet_cache[sheet_name]

    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        return None

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        _patch_worksheet_read_methods(worksheet)
        cache.worksheet_cache[sheet_name] = worksheet
        return worksheet
    except Exception:
        logger.exception("Error opening worksheet: %s", sheet_name)
        return None


def build_idempotency_key(namespace, payload):
    """Build a stable, opaque key from a canonical JSON payload."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{namespace}:v1:{hashlib.sha256(encoded).hexdigest()}"


def ensure_sheet_headers(sheet, required_headers, *, op_name="sheets.headers"):
    """Read and add missing headers using the shared bounded retry policy."""
    from database.retry import retry_sheet_op

    values = retry_sheet_op(
        lambda: sheet.get_all_values(), op_name=f"{op_name}.read",
    ) or []
    if not isinstance(values, list):
        values = []
    if not values:
        retry_sheet_op(
            lambda: sheet.append_row(list(required_headers), value_input_option="USER_ENTERED"),
            op_name=f"{op_name}.append",
        )
        return list(required_headers)

    headers = [str(header).strip() for header in values[0]]
    missing = [header for header in required_headers if header not in headers]
    if missing:
        headers.extend(missing)
        end_col = column_number_to_letter(len(headers))
        retry_sheet_op(
            lambda: sheet.update(
                f"A1:{end_col}1", [headers], value_input_option="USER_ENTERED",
            ),
            op_name=f"{op_name}.update",
        )
    return headers


def find_sheet_row_by_key(sheet, key, key_header, *, op_name="sheets.find_key"):
    """Return the newest row matching a key without mutating the sheet."""
    from database.retry import retry_sheet_op

    if not key:
        return None
    values = retry_sheet_op(
        lambda: sheet.get_all_values(), op_name=op_name,
    ) or []
    if not isinstance(values, list):
        values = []
    if len(values) < 2:
        return None
    headers = [str(header).strip() for header in values[0]]
    if key_header not in headers:
        return None
    key_index = headers.index(key_header)
    for row in reversed(values[1:]):
        if len(row) > key_index and str(row[key_index]).strip() == str(key):
            padded = list(row) + [""] * max(0, len(headers) - len(row))
            return dict(zip(headers, padded))
    return None


def append_row_if_absent(
    sheet,
    row,
    key,
    key_header,
    *,
    required_headers=(),
    op_name="sheets.append_idempotent",
):
    """Append once per key; return False when the row already exists."""
    from database.retry import retry_sheet_op

    if not key:
        raise ValueError("idempotency key is required")

    with _IDEMPOTENCY_LOCK:
        headers = ensure_sheet_headers(
            sheet, list(required_headers) or [key_header], op_name=op_name,
        )
        existing = find_sheet_row_by_key(
            sheet, key, key_header, op_name=f"{op_name}.find",
        )
        if existing is not None:
            return False

        padded_row = list(row) + [""] * max(0, len(headers) - len(row))
        key_index = headers.index(key_header)
        if len(padded_row) <= key_index or str(padded_row[key_index]).strip() != str(key):
            raise ValueError(f"row does not contain {key_header}")
        retry_sheet_op(
            lambda: sheet.append_row(padded_row, value_input_option="USER_ENTERED"),
            op_name=op_name,
        )
        return True

def column_number_to_letter(n):
    """
    Convert a 1-based column number to A1-notation letters.
    """
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(ord('A') + remainder) + result
    return result


def save_symptom_data(user_id, pain, wound, fever, mobility, risk_level, risk_score):
    """
    Save symptom report to SymptomLog sheet.

    The append is retried with a small webhook-safe budget. Because Google
    Sheets append is not transactional, an ambiguous timeout can theoretically
    create a duplicate row; this patch keeps the existing at-least-once shape.

    Returns: boolean (success/failure)
    """
    try:
        sheet = get_worksheet(SHEET_SYMPTOM_LOG)
        if not sheet:
            logger.error("No gspread client available")
            return False

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id,
            pain or "",
            wound or "",
            fever or "",
            mobility or "",
            risk_level,
            risk_score
        ]
        
        from database.retry import retry_sheet_op
        retry_sheet_op(
            lambda: sheet.append_row(row, value_input_option='USER_ENTERED'),
            op_name="symptom_log.append",
            max_attempts=2,
            base_delay=0.25,
            max_delay=0.5,
        )
        logger.info("Symptom data saved for user %s", user_id)
        return True
    
    except Exception:
        logger.exception("Error saving symptom data")
        return False


def get_recent_symptom_reports(user_id=None, days=7, limit=50):
    """
    Read recent rows from SymptomLog (Phase 2-D).

    Args:
        user_id: If set, return only reports from this user. If None, return
                 reports from all users (used by the daily early-warning scan).
        days:    Look-back window in days (based on the timestamp column).
        limit:   Safety cap on returned rows (most recent first).

    Returns:
        list[dict]: newest-first rows with keys:
            timestamp (datetime|None), user_id, pain, wound, fever, mobility,
            risk_level, risk_score (int)
        On error or empty sheet, returns [].
    """
    try:
        sheet = get_worksheet(SHEET_SYMPTOM_LOG)
        if not sheet:
            return []

        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return []

        # First row = header; rows added via save_symptom_data have columns:
        # [timestamp, user_id, pain, wound, fever, mobility, risk_level, risk_score]
        cutoff = datetime.now(tz=LOCAL_TZ) - timedelta(days=days)
        out = []
        for row in values[1:]:
            if len(row) < 8:
                continue
            ts_raw = (row[0] or "").strip()
            try:
                ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=LOCAL_TZ)
            except (ValueError, TypeError):
                ts = None
            if ts and ts < cutoff:
                continue
            uid = (row[1] or "").strip()
            if user_id and uid != user_id:
                continue
            try:
                score = int(str(row[7]).strip() or 0)
            except (ValueError, TypeError):
                score = 0
            out.append({
                "timestamp": ts,
                "user_id": uid,
                "pain": row[2],
                "wound": row[3],
                "fever": row[4],
                "mobility": row[5],
                "risk_level": row[6],
                "risk_score": score,
            })

        # Newest first; rows without timestamp are pushed to the end.
        out.sort(key=lambda r: r["timestamp"] or datetime.min.replace(tzinfo=LOCAL_TZ),
                 reverse=True)
        return out[:limit]

    except Exception:
        logger.exception("Error reading symptom reports")
        return []


def save_profile_data(user_id, age, weight, height, bmi, diseases, risk_level, risk_score):
    """
    Save risk profile to RiskProfile sheet
    Returns: boolean (success/failure)
    """
    try:
        sheet = get_worksheet(SHEET_RISK_PROFILE)
        if not sheet:
            logger.error("No gspread client available")
            return False

        from database.retry import retry_sheet_op
        headers = ensure_sheet_headers(
            sheet,
            ["Timestamp", "User_ID", "Age", "Weight", "Height", "BMI", "Diseases", "Risk_Level", "Risk_Score"],
            op_name="risk_profile.headers",
        )
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        diseases_str = ", ".join(diseases) if isinstance(diseases, list) else str(diseases)

        record = {
            "Timestamp": timestamp,
            "User_ID": user_id,
            "Age": age or "",
            "Weight": weight or "",
            "Height": height or "",
            "BMI": f"{bmi:.1f}" if bmi > 0 else "",
            "Diseases": diseases_str,
            "Risk_Level": risk_level,
            "Risk_Score": risk_score,
        }
        row = [record.get(header, "") for header in headers]
        retry_sheet_op(
            lambda: sheet.append_row(row, value_input_option="USER_ENTERED"),
            op_name="risk_profile.append",
        )
        logger.info("Profile data saved for user %s", user_id)
        return True
    
    except Exception:
        logger.exception("Error saving profile data")
        return False


def save_appointment_data(user_id, name, phone, preferred_date, preferred_time,
                          reason, status="New", assigned_to="", notes="",
                          idempotency_key=None):
    """
    Save appointment to Appointments sheet
    Returns: boolean (success/failure)
    """
    try:
        sheet = get_worksheet(SHEET_APPOINTMENTS)
        if not sheet:
            logger.error("No gspread client available")
            return False

        headers = ensure_sheet_headers(
            sheet,
            [
                "Timestamp", "User_ID", "Name", "Phone", "Preferred_Date",
                "Preferred_Time", "Reason", "Status", "Assigned_To", "Notes",
                "Idempotency_Key",
            ],
            op_name="appointments.headers",
        )
        if not idempotency_key:
            idempotency_key = build_idempotency_key(
                "appointment",
                {
                    "user_id": str(user_id or "").strip(),
                    "preferred_date": str(preferred_date or "").strip(),
                    "preferred_time": str(preferred_time or "").strip(),
                    "reason": str(reason or "").strip(),
                },
            )
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "Timestamp": timestamp,
            "User_ID": user_id,
            "Name": name or "",
            "Phone": phone or "",
            "Preferred_Date": preferred_date or "",
            "Preferred_Time": preferred_time or "",
            "Reason": reason or "",
            "Status": status,
            "Assigned_To": assigned_to,
            "Notes": notes,
            "Idempotency_Key": idempotency_key,
        }
        row = [record.get(header, "") for header in headers]
        append_row_if_absent(
            sheet,
            row,
            idempotency_key,
            "Idempotency_Key",
            required_headers=headers,
            op_name="appointments.append",
        )
        logger.info("Appointment saved for user %s", user_id)
        return True
    
    except Exception:
        logger.exception("Error saving appointment data")
        return False
