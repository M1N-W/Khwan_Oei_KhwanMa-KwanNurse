"""Read-only Google Sheets dependency and schema health checks."""
from __future__ import annotations

import os
from typing import Any

from config import (
    GSPREAD_CREDENTIALS,
    SHEET_APPOINTMENTS,
    SHEET_EDUCATION_LOG,
    SHEET_FAILED_NURSE_ALERTS,
    SHEET_FOLLOW_UP_REMINDERS,
    SHEET_PATIENT_PROFILE,
    SHEET_REMINDER_SCHEDULES,
    SHEET_RISK_PROFILE,
    SHEET_SURVEY_SCHEDULES,
    SHEET_SYMPTOM_LOG,
    SHEET_TELECONSULT_QUEUE,
    SHEET_TELECONSULT_SESSIONS,
    SHEET_VOICE_LOG,
    SHEET_WOUND_ANALYSIS_LOG,
)
from database.retry import retry_sheet_op
from database.sheets import get_spreadsheet


REQUIRED_HEADERS: dict[str, tuple[str, ...]] = {
    SHEET_SYMPTOM_LOG: ("Timestamp", "User_ID", "Risk_Level", "Risk_Score"),
    SHEET_RISK_PROFILE: ("Timestamp", "User_ID", "Risk_Level", "Risk_Score"),
    SHEET_APPOINTMENTS: ("Timestamp", "User_ID", "Preferred_Date", "Idempotency_Key"),
    SHEET_FOLLOW_UP_REMINDERS: ("Timestamp", "User_ID", "Reminder_Type", "Status"),
    SHEET_REMINDER_SCHEDULES: ("Created_At", "User_ID", "Scheduled_Date", "Status", "Retry_Count"),
    SHEET_TELECONSULT_SESSIONS: ("Session_ID", "User_ID", "Status", "Idempotency_Key"),
    SHEET_TELECONSULT_QUEUE: ("Queue_ID", "Session_ID", "User_ID", "Status"),
    SHEET_WOUND_ANALYSIS_LOG: ("Timestamp", "User_ID", "Severity"),
    SHEET_PATIENT_PROFILE: ("User_ID", "Registration_Status", "Updated_At"),
    SHEET_EDUCATION_LOG: ("Timestamp", "User_ID", "Topic"),
    SHEET_VOICE_LOG: ("Timestamp", "User_ID", "Status"),
    SHEET_FAILED_NURSE_ALERTS: ("Created_At", "Idempotency_Key", "Status", "Payload_JSON"),
    SHEET_SURVEY_SCHEDULES: ("Created_At", "User_ID", "Tracking_Token", "Status", "Retry_Count"),
}


def check_sheet_health() -> dict[str, Any]:
    """Check credentials, worksheet presence, and required headers without writes."""
    credentials_configured = bool(
        GSPREAD_CREDENTIALS or os.environ.get("GOOGLE_CREDS_B64") or os.path.exists("credentials.json")
    )
    result: dict[str, Any] = {
        "status": "ok",
        "credentials_configured": credentials_configured,
        "spreadsheet_reachable": False,
        "worksheets": {},
    }
    if not credentials_configured:
        result["status"] = "unavailable"
        result["reason"] = "credentials_missing"
        return result

    try:
        spreadsheet = get_spreadsheet()
        if spreadsheet is None:
            result["status"] = "unavailable"
            result["reason"] = "spreadsheet_unreachable"
            return result
        result["spreadsheet_reachable"] = True
        existing = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
        for name, required in REQUIRED_HEADERS.items():
            worksheet = existing.get(name)
            if worksheet is None:
                result["worksheets"][name] = {"status": "missing"}
                continue
            values = retry_sheet_op(
                lambda worksheet=worksheet: worksheet.get_all_values(),
                op_name=f"health.{name}.read",
            ) or []
            headers = [str(header).strip() for header in values[0]] if values else []
            missing = [header for header in required if header not in headers]
            result["worksheets"][name] = {
                "status": "ok" if not missing else "invalid_headers",
                "missing_headers": missing,
                "row_count": max(0, len(values) - 1),
            }
        if any(item["status"] != "ok" for item in result["worksheets"].values()):
            result["status"] = "degraded"
    except Exception as exc:
        result["status"] = "error"
        result["reason"] = type(exc).__name__
    return result
