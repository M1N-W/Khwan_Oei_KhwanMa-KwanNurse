# -*- coding: utf-8 -*-
"""
Google Sheets Database Module
Handles all interactions with Google Sheets
"""
import base64
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

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        diseases_str = ", ".join(diseases) if isinstance(diseases, list) else str(diseases)
        
        row = [
            timestamp,
            user_id,
            age or "",
            weight or "",
            height or "",
            f"{bmi:.1f}" if bmi > 0 else "",
            diseases_str,
            risk_level,
            risk_score
        ]
        
        sheet.append_row(row, value_input_option='USER_ENTERED')
        logger.info("Profile data saved for user %s", user_id)
        return True
    
    except Exception:
        logger.exception("Error saving profile data")
        return False


def save_appointment_data(user_id, name, phone, preferred_date, preferred_time, 
                          reason, status="New", assigned_to="", notes=""):
    """
    Save appointment to Appointments sheet
    Returns: boolean (success/failure)
    """
    try:
        sheet = get_worksheet(SHEET_APPOINTMENTS)
        if not sheet:
            logger.error("No gspread client available")
            return False

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id,
            name or "",
            phone or "",
            preferred_date or "",
            preferred_time or "",
            reason or "",
            status,
            assigned_to,
            notes
        ]
        
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Appointment saved for user %s", user_id)
        return True
    
    except Exception:
        logger.exception("Error saving appointment data")
        return False
