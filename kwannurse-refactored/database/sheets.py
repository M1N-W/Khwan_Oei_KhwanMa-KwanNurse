# -*- coding: utf-8 -*-
"""
Google Sheets Database Module
Handles all interactions with Google Sheets
"""
import gspread
import json
import os
from datetime import datetime
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

# Module-level client cache
_sheet_client = None


def get_sheet_client():
    """
    Get Google Sheets client (singleton pattern)
    Returns: gspread client or None
    """
    global _sheet_client
    
    if _sheet_client is not None:
        return _sheet_client
    
    try:
        creds_env = GSPREAD_CREDENTIALS
        if creds_env:
            creds_json = json.loads(creds_env)
            if hasattr(gspread, "service_account_from_dict"):
                _sheet_client = gspread.service_account_from_dict(creds_json)
                logger.info("Google Sheets client initialized from environment")
                return _sheet_client
        
        if os.path.exists("credentials.json"):
            _sheet_client = gspread.service_account(filename="credentials.json")
            logger.info("Google Sheets client initialized from file")
            return _sheet_client
        
        logger.warning("No Google credentials found")
    except Exception:
        logger.exception("Error connecting to Google Sheets")
    
    return None


def save_symptom_data(user_id, pain, wound, fever, mobility, risk_level, risk_score):
    """
    Save symptom report to SymptomLog sheet
    Returns: boolean (success/failure)
    """
    try:
        client = get_sheet_client()
        if not client:
            logger.error("No gspread client available")
            return False
        
        spreadsheet = client.open(SPREADSHEET_NAME)
        sheet = spreadsheet.worksheet(SHEET_SYMPTOM_LOG)
        
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


def save_profile_data(user_id, age, weight, height, bmi, diseases, risk_level, risk_score):
    """
    Save risk profile to RiskProfile sheet
    Returns: boolean (success/failure)
    """
    try:
        client = get_sheet_client()
        if not client:
            logger.error("No gspread client available")
            return False
        
        spreadsheet = client.open(SPREADSHEET_NAME)
        sheet = spreadsheet.worksheet(SHEET_RISK_PROFILE)
        
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
        client = get_sheet_client()
        if not client:
            logger.error("No gspread client available")
            return False
        
        spreadsheet = client.open(SPREADSHEET_NAME)
        sheet = spreadsheet.worksheet(SHEET_APPOINTMENTS)
        
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
