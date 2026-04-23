# -*- coding: utf-8 -*-
"""
Google Sheets Database Module
Handles all interactions with Google Sheets
"""
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

# Module-level client cache with TTL (refresh before 1-hour token expiry)
_sheet_client = None
_client_created_at = None
_spreadsheet = None
_spreadsheet_created_at = None
_worksheet_cache = {}
_CLIENT_TTL_SECONDS = 3000  # 50 minutes


def _reset_sheet_cache():
    """Reset cached spreadsheet and worksheet handles when the client refreshes."""
    global _spreadsheet, _spreadsheet_created_at, _worksheet_cache
    _spreadsheet = None
    _spreadsheet_created_at = None
    _worksheet_cache = {}


def get_sheet_client():
    """
    Get Google Sheets client (singleton with TTL refresh)
    Refreshes the client before the 1-hour OAuth token expiry.
    Returns: gspread client or None
    """
    global _sheet_client, _client_created_at

    now = time.monotonic()
    if (_sheet_client is not None and
            _client_created_at is not None and
            (now - _client_created_at) < _CLIENT_TTL_SECONDS):
        return _sheet_client

    # Invalidate stale client
    _sheet_client = None
    _client_created_at = None
    _reset_sheet_cache()

    try:
        creds_env = GSPREAD_CREDENTIALS
        if creds_env:
            creds_json = json.loads(creds_env)
            if hasattr(gspread, "service_account_from_dict"):
                _sheet_client = gspread.service_account_from_dict(creds_json)
                _client_created_at = now
                logger.info("Google Sheets client initialized from environment")
                return _sheet_client

        if os.path.exists("credentials.json"):
            _sheet_client = gspread.service_account(filename="credentials.json")
            _client_created_at = now
            logger.info("Google Sheets client initialized from file")
            return _sheet_client

        logger.warning("No Google credentials found")
    except Exception:
        logger.exception("Error connecting to Google Sheets")

    return None


def get_spreadsheet():
    """
    Get the target spreadsheet using the shared client/cache lifecycle.
    """
    global _spreadsheet, _spreadsheet_created_at

    now = time.monotonic()
    if (_spreadsheet is not None and
            _spreadsheet_created_at is not None and
            (now - _spreadsheet_created_at) < _CLIENT_TTL_SECONDS):
        return _spreadsheet

    client = get_sheet_client()
    if not client:
        return None

    try:
        _spreadsheet = client.open(SPREADSHEET_NAME)
        _spreadsheet_created_at = now
        _worksheet_cache.clear()
        return _spreadsheet
    except Exception:
        logger.exception("Error opening Google Spreadsheet: %s", SPREADSHEET_NAME)
        _reset_sheet_cache()
        return None


def get_worksheet(sheet_name):
    """
    Get a worksheet handle with the same TTL lifecycle as the sheet client.
    """
    if sheet_name in _worksheet_cache:
        return _worksheet_cache[sheet_name]

    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        return None

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        _worksheet_cache[sheet_name] = worksheet
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
    Save symptom report to SymptomLog sheet
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
        
        sheet.append_row(row, value_input_option='USER_ENTERED')
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
