# -*- coding: utf-8 -*-
"""
Reminder Database Module
Handle all database operations for follow-up reminders

⚠️ THIS IS: database/reminders.py (PLURAL - in database folder)
⚠️ NOT: services/reminder.py (that's a different file!)
"""
from datetime import datetime, timedelta
from typing import Any, Optional
from config import (
    LOCAL_TZ,
    SHEET_FOLLOW_UP_REMINDERS,
    SHEET_REMINDER_SCHEDULES,
    ReminderStatus,
    get_logger,
)
from database.sheets import get_worksheet, column_number_to_letter

REQUIRED_HEADERS = [
    "Created_At",
    "User_ID",
    "Discharge_Date",
    "Reminder_Type",
    "Scheduled_Date",
    "Status",
    "Notes",
    "Claimed_By",
    "Claimed_At",
    "Retry_Count",
    "Last_Error",
    "Last_Attempt_At"
]

logger = get_logger(__name__)


def save_reminder_schedule(user_id, discharge_date, reminder_type, scheduled_date, notes=""):
    """
    Save a scheduled reminder to database
    
    Args:
        user_id: User ID
        discharge_date: Date of discharge
        reminder_type: Type of reminder (day3, day7, day14, day30, custom)
        scheduled_date: When reminder should be sent
        notes: Optional notes
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            logger.error("No sheet client available")
            return False
        
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        discharge_str = discharge_date.strftime("%Y-%m-%d") if isinstance(discharge_date, datetime) else str(discharge_date)
        scheduled_str = scheduled_date.strftime("%Y-%m-%d %H:%M:%S") if isinstance(scheduled_date, datetime) else str(scheduled_date)
        
        row = [
            timestamp,           # Created_At
            user_id,            # User_ID
            discharge_str,      # Discharge_Date
            reminder_type,      # Reminder_Type
            scheduled_str,      # Scheduled_Date
            ReminderStatus.SCHEDULED,  # Status
            notes               # Notes
        ]
        
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Scheduled {reminder_type} reminder for user {user_id} at {scheduled_str}")
        return True
        
    except Exception as e:
        logger.exception(f"Error saving reminder schedule: {e}")
        return False


def save_reminder_sent(user_id, reminder_type, message_text=""):
    """
    Record that a reminder was sent
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder
        message_text: The message that was sent
        
    Returns:
        bool: True if successful
    """
    try:
        sheet = get_worksheet(SHEET_FOLLOW_UP_REMINDERS)
        if not sheet:
            logger.error("No sheet client available")
            return False
        
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        row = [
            timestamp,          # Timestamp
            user_id,           # User_ID
            reminder_type,     # Reminder_Type
            ReminderStatus.SENT,  # Status
            '',                # Response_Text (empty for now)
            message_text,      # Message_Sent
            ''                 # Response_Timestamp (empty for now)
        ]
        
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Recorded reminder sent: {reminder_type} to {user_id}")
        
        # Update schedule status
        update_schedule_status(user_id, reminder_type, ReminderStatus.SENT)
        
        return True
        
    except Exception as e:
        logger.exception(f"Error saving reminder sent: {e}")
        return False


def save_reminder_response(user_id, reminder_type, response_text):
    """
    Record user's response to a reminder
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder
        response_text: User's response
        
    Returns:
        bool: True if successful
    """
    try:
        sheet = get_worksheet(SHEET_FOLLOW_UP_REMINDERS)
        if not sheet:
            logger.error("No sheet client available")
            return False
        
        # Get all values safely
        all_values = sheet.get_all_values()
        
        # If sheet is not empty, try to find matching record
        if all_values and len(all_values) > 1:
            headers = all_values[0]
            
            # Search backwards (most recent first)
            for i in range(len(all_values) - 1, 0, -1):  # Start from end, skip header
                row = all_values[i]
                if len(row) >= len(headers):
                    record = dict(zip(headers, row))
                    
                    if (record.get('User_ID') == user_id and 
                        record.get('Reminder_Type') == reminder_type and
                        record.get('Status') == ReminderStatus.SENT):
                        
                        # Update this row using batch_update (single API call)
                        row_num = i + 1  # +1 for 1-indexed
                        response_timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")

                        status_col = headers.index('Status') + 1 if 'Status' in headers else 4
                        response_col = headers.index('Response_Text') + 1 if 'Response_Text' in headers else 5
                        timestamp_col = headers.index('Response_Timestamp') + 1 if 'Response_Timestamp' in headers else 7

                        sheet.batch_update([
                            {
                                'range': f"{column_number_to_letter(status_col)}{row_num}",
                                'values': [[ReminderStatus.RESPONDED]]
                            },
                            {
                                'range': f"{column_number_to_letter(response_col)}{row_num}",
                                'values': [[response_text]]
                            },
                            {
                                'range': f"{column_number_to_letter(timestamp_col)}{row_num}",
                                'values': [[response_timestamp]]
                            }
                        ])
                        
                        logger.info(f"Recorded response from {user_id} for {reminder_type}")
                        
                        # Update schedule status
                        update_schedule_status(user_id, reminder_type, ReminderStatus.RESPONDED)
                        
                        return True
        
        # If no 'sent' record found, create a new 'responded' record anyway
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id,
            reminder_type,
            ReminderStatus.RESPONDED,
            response_text,
            '',  # Message_Sent (unknown)
            timestamp  # Response_Timestamp
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.warning(f"No 'sent' record found for {user_id}/{reminder_type}, created new responded record")
        
        return True
        
    except Exception as e:
        logger.exception(f"Error saving reminder response: {e}")
        return False


def update_schedule_status(user_id, reminder_type, new_status):
    """
    Update the status of a scheduled reminder
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder
        new_status: New status (sent, responded, no_response)
    """
    try:
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            return
        
        # Get all values safely
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            logger.warning("ReminderSchedules sheet is empty, cannot update status")
            return
        
        headers = all_values[0]
        
        # Find status column index
        status_col = headers.index('Status') + 1 if 'Status' in headers else 6
        
        # Find the matching schedule (search backwards for most recent)
        for i in range(len(all_values) - 1, 0, -1):
            row = all_values[i]
            if len(row) >= len(headers):
                record = dict(zip(headers, row))
                
                if (record.get('User_ID') == user_id and
                    record.get('Reminder_Type') == reminder_type):

                    row_num = i + 1
                    sheet.batch_update([{
                        'range': f"{column_number_to_letter(status_col)}{row_num}",
                        'values': [[new_status]]
                    }])
                    logger.info(f"Updated schedule status: {user_id}/{reminder_type} -> {new_status}")
                    return
                
    except Exception as e:
        logger.exception(f"Error updating schedule status: {e}")


def get_pending_reminders(user_id, reminder_type):
    """
    Get pending reminders for a user
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder (optional, None for all)
        
    Returns:
        list: List of pending reminders
    """
    try:
        sheet = get_worksheet(SHEET_FOLLOW_UP_REMINDERS)
        if not sheet:
            return []
        
        # Get all values safely
        all_values = sheet.get_all_values()
        
        # Check if empty
        if not all_values or len(all_values) <= 1:
            logger.info("FollowUpReminders sheet is empty")
            return []
        
        # Parse records
        headers = all_values[0]
        records = []
        
        for row in all_values[1:]:
            if len(row) >= len(headers):
                record = dict(zip(headers, row))
                records.append(record)
        
        # Filter pending reminders
        pending = []
        for record in records:
            if record.get('User_ID') == user_id and record.get('Status') == ReminderStatus.SENT:
                if reminder_type is None or record.get('Reminder_Type') == reminder_type:
                    pending.append(record)
        
        return pending
        
    except Exception as e:
        logger.exception(f"Error getting pending reminders: {e}")
        return []


def get_scheduled_reminders():
    """
    Get all scheduled reminders that haven't been sent yet
    
    Returns:
        list: List of scheduled reminders
    """
    try:
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            return []
        
        # Get all values (safer than get_all_records for empty sheets)
        all_values = sheet.get_all_values()
        
        # Check if sheet is empty or has only headers
        if not all_values or len(all_values) <= 1:
            logger.info("ReminderSchedules sheet is empty (no data rows)")
            return []
        
        # Parse records manually
        headers = all_values[0]
        records = []
        
        for row in all_values[1:]:  # Skip header
            if len(row) >= len(headers):
                record = dict(zip(headers, row))
                records.append(record)
        
        # Filter for scheduled status
        scheduled = [r for r in records if r.get('Status') == ReminderStatus.SCHEDULED]
        
        return scheduled
        
    except Exception as e:
        logger.exception(f"Error getting scheduled reminders: {e}")
        return []


def check_no_response_reminders():
    """
    Check for reminders that were sent but user hasn't responded
    
    Returns:
        list: List of reminders with no response after 24 hours
    """
    try:
        sheet = get_worksheet(SHEET_FOLLOW_UP_REMINDERS)
        if not sheet:
            return []
        
        # Get all values safely
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            logger.info("FollowUpReminders sheet is empty, no reminders to check")
            return []
        
        headers = all_values[0]
        
        # Find column indices
        status_col = headers.index('Status') + 1 if 'Status' in headers else 4
        
        no_response = []
        now = datetime.now(tz=LOCAL_TZ)
        
        for i in range(1, len(all_values)):  # Skip header
            row = all_values[i]
            if len(row) >= len(headers):
                record = dict(zip(headers, row))
                
                if record.get('Status') == ReminderStatus.SENT:
                    # Check if sent more than 24 hours ago
                    timestamp_str = record.get('Timestamp', '')
                    if timestamp_str:
                        try:
                            sent_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                            sent_time = sent_time.replace(tzinfo=LOCAL_TZ)
                            
                            hours_passed = (now - sent_time).total_seconds() / 3600

                            if hours_passed >= 24:
                                row_num = i + 1
                                sheet.batch_update([{
                                    'range': f"{column_number_to_letter(status_col)}{row_num}",
                                    'values': [[ReminderStatus.NO_RESPONSE]]
                                }])

                                record['row_num'] = row_num
                                record['hours_passed'] = hours_passed
                                no_response.append(record)
                                
                                # Update schedule status
                                update_schedule_status(
                                    record.get('User_ID'),
                                    record.get('Reminder_Type'),
                                    ReminderStatus.NO_RESPONSE,
                                )
                                
                        except Exception as e:
                            logger.warning(f"Error parsing timestamp {timestamp_str}: {e}")
        
        logger.info(f"Found {len(no_response)} reminders with no response after 24h")
        return no_response
        
    except Exception as e:
        logger.exception(f"Error checking no-response reminders: {e}")
        return []


def _verify_schedules_headers(sheet) -> list[str]:
    """
    Verify that the ReminderSchedules worksheet contains all required headers.
    If headers are missing, append them dynamically to maintain backward compatibility.
    
    Args:
        sheet: The gspread worksheet object.
        
    Returns:
        list[str]: The validated list of headers present in the sheet.
    """
    from database.retry import retry_sheet_op
    values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="reminders.verify_headers_get_values")
    if not values:
        retry_sheet_op(lambda: sheet.append_row(REQUIRED_HEADERS, value_input_option="USER_ENTERED"), op_name="reminders.verify_headers_append")
        return REQUIRED_HEADERS
        
    headers = [str(h).strip() for h in values[0]]
    changed = False
    
    for h in REQUIRED_HEADERS:
        if h not in headers:
            headers.append(h)
            changed = True
            
    if changed:
        end_col = column_number_to_letter(len(headers))
        updates = [{
            'range': f"A1:{end_col}1",
            'values': [headers]
        }]
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="reminders.verify_headers_update")
        
    return headers


def get_due_reminders(
    now_dt: Optional[datetime] = None,
    max_retries: int = 3,
    lock_duration_minutes: int = 10,
    **kwargs
) -> list[dict]:
    """
    Retrieve all scheduled reminders that are currently due for delivery.
    """
    from database.retry import retry_sheet_op
    try:
        if now_dt is None:
            now_dt = kwargs.get('now')
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)
            
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            logger.error("No worksheet available for SHEET_REMINDER_SCHEDULES")
            return []
            
        headers = _verify_schedules_headers(sheet)
        
        all_values = retry_sheet_op(lambda: sheet.get_all_values(), op_name="reminders.get_all_values")
        
        if not all_values or len(all_values) < 2:
            return []
            
        due_reminders = []
        
        for i, row in enumerate(all_values[1:]):
            row_num = i + 2
            
            padded_row = list(row) + [""] * max(0, len(headers) - len(row))
            record = dict(zip(headers, padded_row))
            
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
                    logger.warning(f"Row {row_num}: Invalid scheduled date format '{sched_str}'")
                    continue
                    
            if scheduled_date > now_dt:
                continue
                
            try:
                retry_count = int(record.get("Retry_Count") or 0)
            except (ValueError, TypeError):
                retry_count = 0
                
            if retry_count >= max_retries:
                continue
                
            claimed_by = record.get("Claimed_By", "").strip()
            if status == "claimed" and claimed_by:
                claimed_at_str = record.get("Claimed_At", "").strip()
                if not claimed_at_str:
                    pass
                else:
                    try:
                        claimed_at = datetime.strptime(claimed_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                        age = now_dt - claimed_at
                        if age < timedelta(minutes=lock_duration_minutes):
                            continue
                    except ValueError:
                        pass
                        
            due_reminders.append({
                "row_num": row_num,
                "user_id": record.get("User_ID"),
                "reminder_type": record.get("Reminder_Type"),
                "scheduled_date": scheduled_date,
                "status": record.get("Status"),
                "retry_count": retry_count,
                "error_msg": record.get("Last_Error", ""),
                
                "Row_Num": row_num,
                "User_ID": record.get("User_ID"),
                "Reminder_Type": record.get("Reminder_Type"),
                "Scheduled_Date": record.get("Scheduled_Date"),
                "Status": record.get("Status"),
                "Retry_Count": retry_count,
                "Error_Msg": record.get("Last_Error", ""),
            })
            
        logger.info(f"Found {len(due_reminders)} due reminders to process.")
        return due_reminders
        
    except Exception as e:
        logger.exception(f"Error in get_due_reminders: {e}")
        return []


def claim_reminder(*args, **kwargs) -> bool:
    """
    Attempt to claim a scheduled reminder row using a write-read lease lock check.
    Supports both old and new signatures.
    """
    from database.retry import retry_sheet_op
    try:
        is_new_sig = False
        if args:
            if isinstance(args[0], int):
                is_new_sig = True
        elif 'row_num' in kwargs:
            is_new_sig = True
            
        if is_new_sig:
            row_num = args[0] if len(args) > 0 else kwargs.get('row_num')
            user_id = args[1] if len(args) > 1 else kwargs.get('user_id')
            reminder_type = args[2] if len(args) > 2 else kwargs.get('reminder_type')
            owner_id = args[3] if len(args) > 3 else kwargs.get('owner_id')
            lock_duration_minutes = args[4] if len(args) > 4 else kwargs.get('lock_duration_minutes', 10)
            now_dt = args[5] if len(args) > 5 else kwargs.get('now_dt', None)
        else:
            user_id = args[0] if len(args) > 0 else kwargs.get('user_id')
            reminder_type = args[1] if len(args) > 1 else kwargs.get('reminder_type')
            row_num = args[2] if len(args) > 2 else kwargs.get('row_num')
            claimed_at = args[3] if len(args) > 3 else kwargs.get('claimed_at', None)
            owner_id = "legacy"
            lock_duration_minutes = 10
            now_dt = claimed_at

        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)
            
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            return False
            
        headers = _verify_schedules_headers(sheet)
        
        current_row = retry_sheet_op(lambda: sheet.row_values(row_num), op_name="reminders.read_target_row")
        
        if not current_row:
            logger.warning(f"Row {row_num}: Could not verify row content. Claim aborted.")
            return False
            
        padded_row = list(current_row) + [""] * max(0, len(headers) - len(current_row))
        record = dict(zip(headers, padded_row))
        
        if record.get("User_ID") != user_id or record.get("Reminder_Type") != reminder_type:
            logger.error(
                f"Concurrency conflict: Row {row_num} contents shifted. "
                f"Expected: User={user_id}, Type={reminder_type}. "
                f"Found: User={record.get('User_ID')}, Type={record.get('Reminder_Type')}."
            )
            return False
            
        status = record.get("Status", "").strip().lower()
        
        if status not in ("scheduled", "failed", "claimed"):
            logger.info(f"Row {row_num} is not in a claimable state. Status: '{status}'")
            return False
            
        if status == "claimed":
            claimed_by = record.get("Claimed_By", "").strip()
            claimed_at_str = record.get("Claimed_At", "").strip()
            if claimed_by and claimed_at_str:
                try:
                    claimed_at = datetime.strptime(claimed_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                    if now_dt - claimed_at < timedelta(minutes=lock_duration_minutes):
                        logger.info(f"Row {row_num}: Active lease held by '{claimed_by}' until {claimed_at + timedelta(minutes=lock_duration_minutes)}")
                        return False
                except ValueError:
                    pass
                    
        status_col = headers.index("Status") + 1
        claimed_by_col = headers.index("Claimed_By") + 1
        claimed_at_col = headers.index("Claimed_At") + 1
        last_attempt_col = headers.index("Last_Attempt_At") + 1
        
        timestamp_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [["claimed"]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[owner_id]]},
            {"range": f"{column_number_to_letter(claimed_at_col)}{row_num}", "values": [[timestamp_str]]},
            {"range": f"{column_number_to_letter(last_attempt_col)}{row_num}", "values": [[timestamp_str]]}
        ]
        
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="reminders.write_claim")
        
        ver_row = retry_sheet_op(lambda: sheet.row_values(row_num), op_name="reminders.verify_claim")
        
        padded_ver_row = list(ver_row) + [""] * max(0, len(headers) - len(ver_row))
        ver_record = dict(zip(headers, padded_ver_row))
        
        ver_owner = ver_record.get("Claimed_By", "").strip()
        ver_status = ver_record.get("Status", "").strip().lower()
        
        if ver_owner == owner_id and ver_status == "claimed" and ver_record.get("User_ID") == user_id:
            logger.info(f"Successfully claimed row {row_num} for user {user_id} with owner ID {owner_id}.")
            return True
        else:
            logger.warning(
                f"Concurrency conflict: Failed to claim row {row_num}. "
                f"Expected Owner: {owner_id}, Status: 'claimed'. "
                f"Actual Owner: {ver_owner}, Status: '{ver_status}'."
            )
            return False
            
    except Exception as e:
        logger.exception(f"Error claiming row {row_num}: {e}")
        return False


def handle_reminder_send_success(
    row_num: int,
    user_id: str,
    reminder_type: str,
    message_text: str = ""
) -> bool:
    """
    Finalize a successfully sent reminder in the database.
    """
    from database.retry import retry_sheet_op
    try:
        sheet_schedules = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet_schedules:
            return False
            
        headers = _verify_schedules_headers(sheet_schedules)
        
        current_row = retry_sheet_op(lambda: sheet_schedules.row_values(row_num), op_name="reminders.read_success_row")
        
        padded_row = list(current_row) + [""] * max(0, len(headers) - len(current_row))
        record = dict(zip(headers, padded_row))
        
        if record.get("User_ID") != user_id or record.get("Reminder_Type") != reminder_type:
            logger.error(
                f"Success handler aborted: Row {row_num} mismatch. "
                f"Expected: User={user_id}, Type={reminder_type}. "
                f"Found: User={record.get('User_ID')}, Type={record.get('Reminder_Type')}."
            )
            return False
            
        status_col = headers.index("Status") + 1
        claimed_by_col = headers.index("Claimed_By") + 1
        claimed_at_col = headers.index("Claimed_At") + 1
        last_attempt_col = headers.index("Last_Attempt_At") + 1
        
        now_str = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [[ReminderStatus.SENT]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(claimed_at_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(last_attempt_col)}{row_num}", "values": [[now_str]]}
        ]
        
        retry_sheet_op(lambda: sheet_schedules.batch_update(updates), op_name="reminders.update_send_success")
        
        sheet_logs = get_worksheet(SHEET_FOLLOW_UP_REMINDERS)
        if sheet_logs:
            timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
            log_row = [
                timestamp,
                user_id,
                reminder_type,
                ReminderStatus.SENT,
                "",
                message_text,
                ""
            ]
            retry_sheet_op(lambda: sheet_logs.append_row(log_row, value_input_option="USER_ENTERED"), op_name="reminders.append_sent_log")
            logger.info(f"Recorded reminder sent event in FollowUpReminders log for {user_id}.")
            
        logger.info(f"Updated ReminderSchedules row {row_num} status to 'sent'.")
        return True
        
    except Exception as e:
        logger.exception(f"Error handling success for row {row_num}: {e}")
        return False


def handle_reminder_send_failure(
    row_num: int,
    user_id: str,
    reminder_type: str,
    error_message: str,
    max_retries: int = 3,
    backoff_minutes: int = 15
) -> bool:
    """
    Handle a failed attempt to send a reminder.
    """
    from database.retry import retry_sheet_op
    try:
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            return False
            
        headers = _verify_schedules_headers(sheet)
        
        current_row = retry_sheet_op(lambda: sheet.row_values(row_num), op_name="reminders.read_failure_row")
        
        padded_row = list(current_row) + [""] * max(0, len(headers) - len(current_row))
        record = dict(zip(headers, padded_row))
        
        if record.get("User_ID") != user_id or record.get("Reminder_Type") != reminder_type:
            logger.error(
                f"Failure handler aborted: Row {row_num} mismatch. "
                f"Expected: User={user_id}, Type={reminder_type}. "
                f"Found: User={record.get('User_ID')}, Type={record.get('Reminder_Type')}."
            )
            return False
            
        try:
            current_retry = int(record.get("Retry_Count") or 0)
        except (ValueError, TypeError):
            current_retry = 0
            
        new_retry_count = max(0, current_retry) + 1
        now = datetime.now(tz=LOCAL_TZ)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        if new_retry_count >= max_retries:
            new_status = ReminderStatus.DEAD_LETTER
            new_scheduled_str = record.get("Scheduled_Date")
            logger.error(f"Row {row_num}: Reminder failed permanently (retry limit {max_retries} exceeded).")
        else:
            new_status = ReminderStatus.FAILED
            backoff_time = now + timedelta(minutes=backoff_minutes)
            new_scheduled_str = backoff_time.strftime("%Y-%m-%d %H:%M:%S")
            logger.warning(
                f"Row {row_num}: Send failed. Retry {new_retry_count}/{max_retries}. "
                f"Backing off scheduled date to {new_scheduled_str}."
            )
            
        status_col = headers.index("Status") + 1
        sched_date_col = headers.index("Scheduled_Date") + 1
        claimed_by_col = headers.index("Claimed_By") + 1
        claimed_at_col = headers.index("Claimed_At") + 1
        retry_count_col = headers.index("Retry_Count") + 1
        last_error_col = headers.index("Last_Error") + 1
        last_attempt_col = headers.index("Last_Attempt_At") + 1
        
        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [[new_status]]},
            {"range": f"{column_number_to_letter(sched_date_col)}{row_num}", "values": [[new_scheduled_str]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(claimed_at_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(retry_count_col)}{row_num}", "values": [[new_retry_count]]},
            {"range": f"{column_number_to_letter(last_error_col)}{row_num}", "values": [[error_message]]},
            {"range": f"{column_number_to_letter(last_attempt_col)}{row_num}", "values": [[now_str]]}
        ]
        
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="reminders.update_send_failure")
        return True
        
    except Exception as e:
        logger.exception(f"Error handling failure for row {row_num}: {e}")
        return False


def update_reminder_result(user_id: str, reminder_type: str, row_num: int, status: str, error_msg: str = '', retry_count: int = 0) -> None:
    """
    Update the status and retry information of a scheduled reminder.
    """
    from database.retry import retry_sheet_op
    try:
        sheet = get_worksheet(SHEET_REMINDER_SCHEDULES)
        if not sheet:
            return
            
        headers = _verify_schedules_headers(sheet)
        
        status_col = headers.index("Status") + 1
        claimed_by_col = headers.index("Claimed_By") + 1
        claimed_at_col = headers.index("Claimed_At") + 1
        retry_count_col = headers.index("Retry_Count") + 1
        last_error_col = headers.index("Last_Error") + 1
        last_attempt_col = headers.index("Last_Attempt_At") + 1
        
        now_str = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        updates = [
            {"range": f"{column_number_to_letter(status_col)}{row_num}", "values": [[status]]},
            {"range": f"{column_number_to_letter(claimed_by_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(claimed_at_col)}{row_num}", "values": [[""]]},
            {"range": f"{column_number_to_letter(retry_count_col)}{row_num}", "values": [[retry_count]]},
            {"range": f"{column_number_to_letter(last_error_col)}{row_num}", "values": [[error_msg]]},
            {"range": f"{column_number_to_letter(last_attempt_col)}{row_num}", "values": [[now_str]]}
        ]
        
        retry_sheet_op(lambda: sheet.batch_update(updates), op_name="reminders.update_reminder_result")
        
        if status == ReminderStatus.SENT:
            sheet_logs = get_worksheet(SHEET_FOLLOW_UP_REMINDERS)
            if sheet_logs:
                timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                log_row = [
                    timestamp,
                    user_id,
                    reminder_type,
                    ReminderStatus.SENT,
                    "",
                    "",
                    ""
                ]
                retry_sheet_op(lambda: sheet_logs.append_row(log_row, value_input_option="USER_ENTERED"), op_name="reminders.append_sent_log")
                
    except Exception as e:
        logger.exception(f"Error in update_reminder_result: {e}")

