# -*- coding: utf-8 -*-
"""
Reminder Database Module
Handle all database operations for follow-up reminders
"""
from datetime import datetime
from config import (
    LOCAL_TZ, 
    SHEET_FOLLOW_UP_REMINDERS,
    SHEET_REMINDER_SCHEDULES,
    get_logger
)
from database.sheets import get_sheet_client

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
        client = get_sheet_client()
        if not client:
            logger.error("No sheet client available")
            return False
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_REMINDER_SCHEDULES)
        
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        discharge_str = discharge_date.strftime("%Y-%m-%d") if isinstance(discharge_date, datetime) else str(discharge_date)
        scheduled_str = scheduled_date.strftime("%Y-%m-%d %H:%M:%S") if isinstance(scheduled_date, datetime) else str(scheduled_date)
        
        row = [
            timestamp,           # Created_At
            user_id,            # User_ID
            discharge_str,      # Discharge_Date
            reminder_type,      # Reminder_Type
            scheduled_str,      # Scheduled_Date
            'scheduled',        # Status
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
        client = get_sheet_client()
        if not client:
            logger.error("No sheet client available")
            return False
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_FOLLOW_UP_REMINDERS)
        
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        row = [
            timestamp,          # Timestamp
            user_id,           # User_ID
            reminder_type,     # Reminder_Type
            'sent',            # Status
            '',                # Response_Text (empty for now)
            message_text,      # Message_Sent
            ''                 # Response_Timestamp (empty for now)
        ]
        
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Recorded reminder sent: {reminder_type} to {user_id}")
        
        # Update schedule status
        update_schedule_status(user_id, reminder_type, 'sent')
        
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
        client = get_sheet_client()
        if not client:
            logger.error("No sheet client available")
            return False
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_FOLLOW_UP_REMINDERS)
        
        # Find the most recent 'sent' entry for this user and reminder type
        all_records = sheet.get_all_records()
        
        # Search backwards (most recent first)
        for i in range(len(all_records) - 1, -1, -1):
            record = all_records[i]
            if (record.get('User_ID') == user_id and 
                record.get('Reminder_Type') == reminder_type and
                record.get('Status') == 'sent'):
                
                # Update this row
                row_num = i + 2  # +2 because: +1 for header, +1 for 0-indexed
                response_timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                
                sheet.update_cell(row_num, 4, 'responded')  # Status
                sheet.update_cell(row_num, 5, response_text)  # Response_Text
                sheet.update_cell(row_num, 7, response_timestamp)  # Response_Timestamp
                
                logger.info(f"Recorded response from {user_id} for {reminder_type}")
                
                # Update schedule status
                update_schedule_status(user_id, reminder_type, 'responded')
                
                return True
        
        # If no 'sent' record found, create a new 'responded' record anyway
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id,
            reminder_type,
            'responded',
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
        client = get_sheet_client()
        if not client:
            return
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_REMINDER_SCHEDULES)
        
        all_records = sheet.get_all_records()
        
        # Find the matching schedule
        for i in range(len(all_records) - 1, -1, -1):
            record = all_records[i]
            if (record.get('User_ID') == user_id and 
                record.get('Reminder_Type') == reminder_type):
                
                row_num = i + 2
                sheet.update_cell(row_num, 6, new_status)  # Status column
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
        client = get_sheet_client()
        if not client:
            return []
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_FOLLOW_UP_REMINDERS)
        
        all_records = sheet.get_all_records()
        
        pending = []
        for record in all_records:
            if record.get('User_ID') == user_id and record.get('Status') == 'sent':
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
        client = get_sheet_client()
        if not client:
            return []
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_REMINDER_SCHEDULES)
        
        all_records = sheet.get_all_records()
        
        scheduled = [r for r in all_records if r.get('Status') == 'scheduled']
        
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
        client = get_sheet_client()
        if not client:
            return []
        
        spreadsheet = client.open('KhwanBot_Data')
        sheet = spreadsheet.worksheet(SHEET_FOLLOW_UP_REMINDERS)
        
        all_records = sheet.get_all_records()
        
        no_response = []
        now = datetime.now(tz=LOCAL_TZ)
        
        for i, record in enumerate(all_records):
            if record.get('Status') == 'sent':
                # Check if sent more than 24 hours ago
                timestamp_str = record.get('Timestamp', '')
                if timestamp_str:
                    try:
                        sent_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                        sent_time = sent_time.replace(tzinfo=LOCAL_TZ)
                        
                        hours_passed = (now - sent_time).total_seconds() / 3600
                        
                        if hours_passed >= 24:
                            # Mark as no_response
                            row_num = i + 2
                            sheet.update_cell(row_num, 4, 'no_response')
                            
                            record['row_num'] = row_num
                            record['hours_passed'] = hours_passed
                            no_response.append(record)
                            
                            # Update schedule status
                            update_schedule_status(
                                record.get('User_ID'),
                                record.get('Reminder_Type'),
                                'no_response'
                            )
                            
                    except Exception as e:
                        logger.warning(f"Error parsing timestamp {timestamp_str}: {e}")
        
        logger.info(f"Found {len(no_response)} reminders with no response after 24h")
        return no_response
        
    except Exception as e:
        logger.exception(f"Error checking no-response reminders: {e}")
        return []
