# -*- coding: utf-8 -*-
"""
Survey Database Module
Handle all database operations for survey schedules and clicks in Google Sheets
"""
from datetime import datetime, timedelta
from typing import Any, Optional
from config import (
    LOCAL_TZ,
    SHEET_SURVEY_SCHEDULES,
    get_logger,
)
from database.sheets import get_worksheet, column_number_to_letter
from database.retry import retry_sheet_op

logger = get_logger(__name__)

REQUIRED_HEADERS = [
    "Created_At",
    "User_ID",
    "Milestone_Day",
    "Survey_URL",
    "Tracking_Token",
    "Status",
    "Sent_At",
    "Clicked_At",
    "Claimed_By",
    "Claimed_At",
    "Retry_Count",
    "Last_Error"
]


def _verify_survey_headers(sheet) -> list[str]:
    """Ensure headers exist, return them."""
    values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_headers")
    if not values or len(values) == 0:
        retry_sheet_op(lambda: sheet.append_row(REQUIRED_HEADERS), op_name="surveys.init_headers")
        return REQUIRED_HEADERS
    return values[0]


def save_survey_schedule(
    user_id: str,
    milestone_day: int,
    survey_url: str,
    tracking_token: str,
    scheduled_at: datetime,
) -> bool:
    """
    Save a scheduled survey to the SurveySchedules sheet.
    We append the target scheduled run time as a note or column.
    Wait, let's look at the columns requested:
    Created_At | User_ID | Milestone_Day | Survey_URL | Tracking_Token | Status | Sent_At | Clicked_At | Claimed_By | Claimed_At | Retry_Count | Last_Error
    Wait, where do we store the Scheduled_Date?
    Let's check: to avoid breaking the header layout or if we want to be fully compliant, let's append 'Scheduled_Date' to the headers, or place it at the end.
    Let's append Scheduled_Date as a new column at the end:
    Created_At | User_ID | Milestone_Day | Survey_URL | Tracking_Token | Status | Sent_At | Clicked_At | Claimed_By | Claimed_At | Retry_Count | Last_Error | Scheduled_Date
    This is extremely clean, preserves the exact 12 columns requested at the front, and adds Scheduled_Date!
    Let's update REQUIRED_HEADERS.
    """
    headers = [
        "Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token",
        "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error",
        "Scheduled_Date"
    ]
    try:
        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            logger.error("No sheet client available for surveys")
            return False

        # Ensure headers
        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_headers")
        if not values or len(values) == 0:
            retry_sheet_op(lambda: sheet.append_row(headers), op_name="surveys.init_headers")
            actual_headers = headers
        else:
            actual_headers = values[0]
            # Migrating headers if Scheduled_Date not present
            if "Scheduled_Date" not in actual_headers:
                actual_headers.append("Scheduled_Date")
                end_col = column_number_to_letter(len(actual_headers))
                retry_sheet_op(lambda: sheet.update(f"A1:{end_col}1", [actual_headers], value_input_option="USER_ENTERED"), op_name="surveys.migrate_headers")

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        sched_str = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")

        row = ["" for _ in actual_headers]
        row[actual_headers.index("Created_At")] = timestamp
        row[actual_headers.index("User_ID")] = user_id
        row[actual_headers.index("Milestone_Day")] = str(milestone_day)
        row[actual_headers.index("Survey_URL")] = survey_url
        row[actual_headers.index("Tracking_Token")] = tracking_token
        row[actual_headers.index("Status")] = "scheduled"
        row[actual_headers.index("Scheduled_Date")] = sched_str
        row[actual_headers.index("Retry_Count")] = "0"

        retry_sheet_op(lambda: sheet.append_row(row, value_input_option="USER_ENTERED"), op_name="surveys.append_schedule")
        logger.info("Saved survey schedule for user=%s milestone=%d scheduled=%s", user_id, milestone_day, sched_str)
        return True
    except Exception:
        logger.exception("Error saving survey schedule")
        return False


def has_scheduled_surveys(user_id: str) -> bool:
    """Check if the user already has survey schedules to prevent duplication."""
    try:
        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return False
        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_all_values")
        if not values or len(values) < 2:
            return False
        headers = values[0]
        if "User_ID" not in headers:
            return False
        idx_uid = headers.index("User_ID")
        for row in values[1:]:
            if len(row) > idx_uid and row[idx_uid] == user_id:
                return True
        return False
    except Exception:
        logger.exception("Error in has_scheduled_surveys")
        return False


def get_due_surveys(
    now_dt: Optional[datetime] = None,
    max_retries: int = 3,
    lock_duration_minutes: int = 10,
) -> list[dict]:
    """Retrieve all scheduled surveys that are currently due/overdue for delivery."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)

        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return []

        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_all_values")
        if not values or len(values) < 2:
            return []

        headers = values[0]
        # Make sure Scheduled_Date column is present in headers
        if "Scheduled_Date" not in headers or "Status" not in headers:
            return []

        due_surveys = []
        for i, row in enumerate(values[1:]):
            row_num = i + 2
            padded = list(row) + [""] * max(0, len(headers) - len(row))
            record = dict(zip(headers, padded))

            status = record.get("Status", "").strip().lower()
            if status not in ("scheduled", "failed", "claimed"):
                continue

            sched_str = record.get("Scheduled_Date", "").strip()
            if not sched_str:
                continue

            try:
                scheduled_date = datetime.strptime(sched_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
            except ValueError:
                try:
                    scheduled_date = datetime.strptime(sched_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
                except ValueError:
                    continue

            if scheduled_date > now_dt:
                continue

            # Check retries
            try:
                retries = int(record.get("Retry_Count") or 0)
            except (ValueError, TypeError):
                retries = 0

            if retries >= max_retries:
                continue

            # Check leases
            if status == "claimed":
                claimed_at_str = record.get("Claimed_At", "").strip()
                if claimed_at_str:
                    try:
                        claimed_at = datetime.strptime(claimed_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                        if (now_dt - claimed_at).total_seconds() < lock_duration_minutes * 60:
                            # Still locked
                            continue
                    except ValueError:
                        pass

            due_surveys.append({
                "row_num": row_num,
                "user_id": record.get("User_ID"),
                "milestone_day": int(record.get("Milestone_Day") or 0),
                "survey_url": record.get("Survey_URL"),
                "tracking_token": record.get("Tracking_Token"),
                "status": status,
                "retry_count": retries,
            })

        return due_surveys
    except Exception:
        logger.exception("Error in get_due_surveys")
        return []


def claim_survey(
    row_num: int,
    user_id: str,
    owner_id: str,
    lock_duration_minutes: int = 10,
    now_dt: Optional[datetime] = None,
) -> bool:
    """Optimistically lock a survey schedule row via Claimed_By/Claimed_At check-and-set."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)

        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return False

        headers = sheet.get_all_values()[0]
        
        # Read-back verification to prevent concurrency conflicts
        row_vals = retry_sheet_op(lambda: sheet.row_values(row_num), op_name="surveys.read_row")
        padded = list(row_vals) + [""] * max(0, len(headers) - len(row_vals))
        record = dict(zip(headers, padded))

        if record.get("User_ID") != user_id:
            return False

        status = record.get("Status", "").strip().lower()
        if status not in ("scheduled", "failed", "claimed"):
            return False

        if status == "claimed":
            claimed_by = record.get("Claimed_By", "").strip()
            claimed_at_str = record.get("Claimed_At", "").strip()
            if claimed_by and claimed_by != owner_id and claimed_at_str:
                try:
                    claimed_at = datetime.strptime(claimed_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                    if (now_dt - claimed_at).total_seconds() < lock_duration_minutes * 60:
                        # Owned by another worker and lease not expired
                        return False
                except ValueError:
                    pass

        # Perform atomic update
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        status_col = headers.index("Status") + 1
        claimed_by_col = headers.index("Claimed_By") + 1
        claimed_at_col = headers.index("Claimed_At") + 1

        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [["claimed"]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[owner_id]]},
            {"range": f"{column_number_to_letter(claimed_at_col)}{row_num}", "values": [[now_str]]},
        ]
        
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="surveys.write_claim")

        # Double check to verify we won the write
        row_vals_verify = retry_sheet_op(lambda: sheet.row_values(row_num), op_name="surveys.verify_claim")
        padded_verify = list(row_vals_verify) + [""] * max(0, len(headers) - len(row_vals_verify))
        record_verify = dict(zip(headers, padded_verify))

        if record_verify.get("Claimed_By") == owner_id:
            logger.info("Claimed survey row %d for user=%s", row_num, user_id)
            return True

        return False
    except Exception:
        logger.exception("Error claiming survey row=%d", row_num)
        return False


def mark_survey_sent(row_num: int, user_id: str, now_dt: Optional[datetime] = None) -> bool:
    """Mark a survey schedule row as sent and record delivery timestamp."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)

        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return False

        headers = sheet.get_all_values()[0]
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        status_col = headers.index("Status") + 1
        sent_at_col = headers.index("Sent_At") + 1
        claimed_by_col = headers.index("Claimed_By") + 1

        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [["sent"]]},
            {"range": f"{column_number_to_letter(sent_at_col)}{row_num}", "values": [[now_str]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[""]]},
        ]
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="surveys.mark_sent")
        logger.info("Marked survey row %d sent for user=%s", row_num, user_id)
        return True
    except Exception:
        logger.exception("Error marking survey sent row=%d", row_num)
        return False


def mark_survey_clicked(token: str, now_dt: Optional[datetime] = None) -> Optional[str]:
    """Resolve tracking token to its schedule row, record click timestamp, and return the Survey_URL."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)

        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return None

        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_all_values")
        if not values or len(values) < 2:
            return None

        headers = values[0]
        if "Tracking_Token" not in headers:
            return None

        idx_token = headers.index("Tracking_Token")
        idx_clicked = headers.index("Clicked_At")
        idx_status = headers.index("Status")
        idx_url = headers.index("Survey_URL") if "Survey_URL" in headers else -1

        for i, row in enumerate(values[1:]):
            row_num = i + 2
            if len(row) > idx_token and row[idx_token] == token:
                now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
                updates = [
                    {"range": f"{column_number_to_letter(idx_clicked + 1)}{row_num}", "values": [[now_str]]},
                    {"range": f"{column_number_to_letter(idx_status + 1)}{row_num}", "values": [["clicked"]]}
                ]
                retry_sheet_op(lambda: sheet.batch_update(updates), op_name="surveys.mark_clicked")
                logger.info("Marked survey clicked for token=%s row=%d", token, row_num)
                
                survey_url = row[idx_url] if (idx_url != -1 and len(row) > idx_url) else ""
                return survey_url or "https://docs.google.com/forms/d/e/1FAIpQLSc8NM7wvIrhzo8zW6NbfvKI741KcEANGzc8BcdZsfCErqkQAQ/viewform"

        return None
    except Exception:
        logger.exception("Error marking survey clicked for token=%s", token)
        return None


def handle_survey_failure(
    row_num: int,
    user_id: str,
    error_message: str,
    max_retries: int = 3,
    backoff_minutes: int = 15,
    now_dt: Optional[datetime] = None,
) -> bool:
    """Handle a failed survey delivery by updating retries and status."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)

        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return False

        headers = sheet.get_all_values()[0]
        
        row_vals = retry_sheet_op(lambda: sheet.row_values(row_num), op_name="surveys.read_row")
        padded = list(row_vals) + [""] * max(0, len(headers) - len(row_vals))
        record = dict(zip(headers, padded))

        try:
            current_retries = int(record.get("Retry_Count") or 0)
        except (ValueError, TypeError):
            current_retries = 0

        next_retries = current_retries + 1
        
        # Dead letter if we hit max retries
        new_status = "dead_letter" if next_retries >= max_retries else "failed"

        # Calculate next Scheduled_Date if failed (exponential backoff)
        if new_status == "failed":
            next_run = now_dt + timedelta(minutes=backoff_minutes * next_retries)
            next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
        else:
            next_run_str = record.get("Scheduled_Date", "")

        status_col = headers.index("Status") + 1
        retry_col = headers.index("Retry_Count") + 1
        error_col = headers.index("Last_Error") + 1
        claimed_by_col = headers.index("Claimed_By") + 1
        sched_col = headers.index("Scheduled_Date") + 1

        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [[new_status]]},
            {"range": f"{column_number_to_letter(retry_col)}{row_num}", "values": [[str(next_retries)]]},
            {"range": f"{column_number_to_letter(error_col)}{row_num}", "values": [[error_message[:200]]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(sched_col)}{row_num}", "values": [[next_run_str]]},
        ]
        
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="surveys.write_failure")
        logger.info("Recorded survey failure for user=%s row=%d (status=%s retries=%d)", user_id, row_num, new_status, next_retries)
        return True
    except Exception:
        logger.exception("Error handling survey failure row=%d", row_num)
        return False


def get_survey_summary_for_user(user_id: str, now_dt: Optional[datetime] = None) -> dict[str, int]:
    """Get survey counts (sent, clicked, overdue) for a user."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return {"sent": 0, "clicked": 0, "overdue": 0}

        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_all_values")
        if not values or len(values) < 2:
            return {"sent": 0, "clicked": 0, "overdue": 0}

        headers = values[0]
        if "User_ID" not in headers:
            return {"sent": 0, "clicked": 0, "overdue": 0}

        idx_uid = headers.index("User_ID")
        idx_status = headers.index("Status")
        idx_sent = headers.index("Sent_At") if "Sent_At" in headers else -1

        sent = 0
        clicked = 0
        overdue = 0

        for row in values[1:]:
            if len(row) <= idx_uid or row[idx_uid] != user_id:
                continue

            status = row[idx_status].strip().lower()
            if status in ("sent", "clicked"):
                sent += 1
            if status == "clicked":
                clicked += 1
            
            # Check overdue (sent but not clicked after 72 hours)
            if status == "sent" and idx_sent != -1 and len(row) > idx_sent and row[idx_sent]:
                try:
                    sent_at = datetime.strptime(row[idx_sent].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                    if (now_dt - sent_at).total_seconds() > 72 * 60 * 60:
                        overdue += 1
                except ValueError:
                    pass

        return {"sent": sent, "clicked": clicked, "overdue": overdue}
    except Exception:
        logger.exception("Error in get_survey_summary_for_user")
        return {"sent": 0, "clicked": 0, "overdue": 0}


def get_patient_survey_timeline(user_id: str, now_dt: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Get list of survey milestones and their delivery details for a user."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return []

        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_all_values")
        if not values or len(values) < 2:
            return []

        headers = values[0]
        if "User_ID" not in headers:
            return []

        idx_uid = headers.index("User_ID")
        idx_milestone = headers.index("Milestone_Day")
        idx_status = headers.index("Status")
        idx_sched = headers.index("Scheduled_Date") if "Scheduled_Date" in headers else -1
        idx_sent = headers.index("Sent_At") if "Sent_At" in headers else -1
        idx_clicked = headers.index("Clicked_At") if "Clicked_At" in headers else -1

        timeline = []
        for row in values[1:]:
            if len(row) <= idx_uid or row[idx_uid] != user_id:
                continue

            padded = list(row) + [""] * max(0, len(headers) - len(row))
            record = dict(zip(headers, padded))

            status = record.get("Status", "").strip().lower()
            sent_at_str = record.get("Sent_At", "").strip()
            
            is_overdue = False
            if status == "sent" and sent_at_str:
                try:
                    sent_at = datetime.strptime(sent_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                    if (now_dt - sent_at).total_seconds() > 72 * 60 * 60:
                        is_overdue = True
                except ValueError:
                    pass

            timeline.append({
                "milestone_day": int(record.get("Milestone_Day") or 0),
                "scheduled_date": record.get("Scheduled_Date", "").strip(),
                "status": status,
                "sent_at": sent_at_str,
                "clicked_at": record.get("Clicked_At", "").strip(),
                "is_overdue": is_overdue,
            })

        timeline.sort(key=lambda item: item["milestone_day"])
        return timeline
    except Exception:
        logger.exception("Error in get_patient_survey_timeline")
        return []


def get_survey_analytics(now_dt: Optional[datetime] = None) -> dict[str, Any]:
    """Get aggregate funnel metrics across all users."""
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        sheet = get_worksheet(SHEET_SURVEY_SCHEDULES)
        if not sheet:
            return {"sent": 0, "clicked": 0, "ctr": 0.0, "failed": 0, "scheduled": 0}

        values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="surveys.get_all_values")
        if not values or len(values) < 2:
            return {"sent": 0, "clicked": 0, "ctr": 0.0, "failed": 0, "scheduled": 0}

        headers = values[0]
        idx_status = headers.index("Status")

        sent = 0
        clicked = 0
        failed = 0
        scheduled = 0

        for row in values[1:]:
            if len(row) <= idx_status:
                continue
            status = row[idx_status].strip().lower()
            if status in ("sent", "clicked"):
                sent += 1
            if status == "clicked":
                clicked += 1
            elif status == "failed" or status == "dead_letter":
                failed += 1
            elif status == "scheduled":
                scheduled += 1

        ctr = (clicked / sent * 100) if sent > 0 else 0.0
        return {
            "sent": sent,
            "clicked": clicked,
            "ctr": round(ctr, 1),
            "failed": failed,
            "scheduled": scheduled,
        }
    except Exception:
        logger.exception("Error in get_survey_analytics")
        return {"sent": 0, "clicked": 0, "ctr": 0.0, "failed": 0, "scheduled": 0}
