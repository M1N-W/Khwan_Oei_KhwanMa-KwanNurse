# -*- coding: utf-8 -*-
"""
Teleconsult Database Module
Handle all database operations for teleconsult sessions and queue
"""
import uuid
from datetime import datetime
from config import (
    LOCAL_TZ,
    SHEET_TELECONSULT_SESSIONS,
    SHEET_TELECONSULT_QUEUE,
    SessionStatus,
    QueueStatus,
    ACTIVE_SESSION_STATUSES,
    get_logger,
)
from database.sheets import get_worksheet, column_number_to_letter

logger = get_logger(__name__)


def generate_session_id():
    """Generate unique session ID"""
    return f"TC{datetime.now(tz=LOCAL_TZ).strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:8]}"


def generate_queue_id():
    """Generate unique queue ID"""
    return f"Q{datetime.now(tz=LOCAL_TZ).strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:6]}"


def create_session(user_id, issue_type, priority, description=""):
    """
    Create a new teleconsult session
    
    Args:
        user_id: Patient's LINE user ID
        issue_type: Category (emergency, medication, wound, appointment, other)
        priority: Priority level (1=high, 2=medium, 3=low)
        description: User's description of issue
        
    Returns:
        dict: Session info or None if failed
    """
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_SESSIONS)
        if not sheet:
            logger.error("No sheet client available")
            return None
        
        session_id = generate_session_id()
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        row = [
            session_id,         # Session_ID
            timestamp,          # Timestamp
            user_id,           # User_ID
            issue_type,        # Issue_Type
            str(priority),     # Priority
            SessionStatus.QUEUED,  # Status
            description,       # Description
            '',                # Queue_Position (set later)
            '',                # Assigned_Nurse
            '',                # Started_At
            '',                # Completed_At
            ''                 # Notes
        ]
        
        sheet.append_row(row, value_input_option="USER_ENTERED")
        
        logger.info(f"Created teleconsult session: {session_id} for {user_id}")
        
        return {
            'session_id': session_id,
            'user_id': user_id,
            'issue_type': issue_type,
            'priority': priority,
            'description': description,
            'status': 'queued',
            'timestamp': timestamp
        }
        
    except Exception as e:
        logger.exception(f"Error creating session: {e}")
        return None


def add_to_queue(session_id, user_id, issue_type, priority):
    """
    Add session to queue
    
    Args:
        session_id: Session ID
        user_id: Patient ID
        issue_type: Issue category
        priority: Priority (1-3)
        
    Returns:
        dict: Queue info including position
    """
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_QUEUE)
        if not sheet:
            return None
        
        # Get current queue to calculate position
        all_values = sheet.get_all_values()
        
        # Count waiting entries
        waiting_count = 0
        if all_values and len(all_values) > 1:
            headers = all_values[0]
            for row in all_values[1:]:
                if len(row) >= len(headers):
                    record = dict(zip(headers, row))
                    if record.get('Status') == QueueStatus.WAITING:
                        waiting_count += 1
        
        queue_position = waiting_count + 1
        
        queue_id = generate_queue_id()
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate estimated wait time (Bug #5 fix)
        # Formula: people-ahead * avg_service_time + own max_wait
        # avg_service_time ≈ 10 minutes per session (conservative estimate)
        from config import ISSUE_CATEGORIES
        max_wait = ISSUE_CATEGORIES.get(issue_type, {}).get('max_wait_minutes', 30)
        AVG_SERVICE_MINUTES = 10
        people_ahead = queue_position - 1
        estimated_wait = people_ahead * AVG_SERVICE_MINUTES + max_wait
        
        row = [
            queue_id,              # Queue_ID
            timestamp,             # Timestamp
            session_id,            # Session_ID
            user_id,              # User_ID
            issue_type,           # Issue_Type
            str(priority),        # Priority
            QueueStatus.WAITING,  # Status
            str(estimated_wait)   # Estimated_Wait
        ]
        
        sheet.append_row(row, value_input_option="USER_ENTERED")
        
        # Update session with queue position
        update_session_queue_position(session_id, queue_position)
        
        logger.info(f"Added to queue: {session_id}, position {queue_position}")
        
        return {
            'queue_id': queue_id,
            'session_id': session_id,
            'position': queue_position,
            'estimated_wait': estimated_wait,
            'timestamp': timestamp
        }
        
    except Exception as e:
        logger.exception(f"Error adding to queue: {e}")
        return None


def update_session_status(session_id, new_status, assigned_nurse=None, notes=None):
    """
    Update session status
    
    Args:
        session_id: Session ID
        new_status: New status (queued, in_progress, completed, cancelled)
        assigned_nurse: Nurse ID (optional)
        notes: Additional notes (optional)
        
    Returns:
        bool: Success
    """
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_SESSIONS)
        if not sheet:
            return False
        
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            logger.warning("Sessions sheet is empty")
            return False
        
        headers = all_values[0]
        
        # Find column indices
        status_col = headers.index('Status') + 1 if 'Status' in headers else 6
        nurse_col = headers.index('Assigned_Nurse') + 1 if 'Assigned_Nurse' in headers else 9
        started_col = headers.index('Started_At') + 1 if 'Started_At' in headers else 10
        completed_col = headers.index('Completed_At') + 1 if 'Completed_At' in headers else 11
        notes_col = headers.index('Notes') + 1 if 'Notes' in headers else 12
        
        # Find the session
        for i in range(1, len(all_values)):
            row = all_values[i]
            if len(row) > 0 and row[0] == session_id:
                row_num = i + 1
                timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")

                updates = [{
                    'range': f"{column_number_to_letter(status_col)}{row_num}",
                    'values': [[new_status]]
                }]

                if new_status == SessionStatus.IN_PROGRESS:
                    updates.append({
                        'range': f"{column_number_to_letter(started_col)}{row_num}",
                        'values': [[timestamp]]
                    })
                elif new_status == SessionStatus.COMPLETED:
                    updates.append({
                        'range': f"{column_number_to_letter(completed_col)}{row_num}",
                        'values': [[timestamp]]
                    })

                if assigned_nurse:
                    updates.append({
                        'range': f"{column_number_to_letter(nurse_col)}{row_num}",
                        'values': [[assigned_nurse]]
                    })

                if notes:
                    updates.append({
                        'range': f"{column_number_to_letter(notes_col)}{row_num}",
                        'values': [[notes]]
                    })

                sheet.batch_update(updates)

                logger.info(f"Updated session {session_id} status to {new_status}")
                return True
        
        logger.warning(f"Session {session_id} not found")
        return False
        
    except Exception as e:
        logger.exception(f"Error updating session status: {e}")
        return False


def update_session_queue_position(session_id, position):
    """Update queue position in session"""
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_SESSIONS)
        if not sheet:
            return False
        
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            return False
        
        headers = all_values[0]
        pos_col = headers.index('Queue_Position') + 1 if 'Queue_Position' in headers else 8
        
        for i in range(1, len(all_values)):
            if len(all_values[i]) > 0 and all_values[i][0] == session_id:
                row_num = i + 1
                sheet.update_cell(row_num, pos_col, str(position))
                return True
        
        return False
        
    except Exception as e:
        logger.exception(f"Error updating queue position: {e}")
        return False


def remove_from_queue(session_id):
    """
    Remove session from queue
    
    Args:
        session_id: Session ID to remove
        
    Returns:
        bool: Success
    """
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_QUEUE)
        if not sheet:
            return False
        
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            return False
        
        headers = all_values[0]
        status_col = headers.index('Status') + 1 if 'Status' in headers else 7
        
        # Find and update status
        for i in range(1, len(all_values)):
            row = all_values[i]
            if len(row) >= 3 and row[2] == session_id:  # Session_ID is column 3
                row_num = i + 1
                sheet.update_cell(row_num, status_col, QueueStatus.REMOVED)
                logger.info(f"Removed session {session_id} from queue")
                return True
        
        return False
        
    except Exception as e:
        logger.exception(f"Error removing from queue: {e}")
        return False


def get_queue_status():
    """
    Get current queue status
    
    Returns:
        dict: Queue information
    """
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_QUEUE)
        if not sheet:
            return {'total': 0, 'by_priority': {}}
        
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            return {'total': 0, 'by_priority': {}}
        
        headers = all_values[0]
        
        waiting = []
        for row in all_values[1:]:
            if len(row) >= len(headers):
                record = dict(zip(headers, row))
                if record.get('Status') == QueueStatus.WAITING:
                    waiting.append(record)
        
        # Count by priority
        by_priority = {1: 0, 2: 0, 3: 0}
        for item in waiting:
            try:
                priority = int(item.get('Priority', 3))
            except (ValueError, TypeError):
                priority = 3
            by_priority[priority] = by_priority.get(priority, 0) + 1
        
        return {
            'total': len(waiting),
            'by_priority': by_priority,
            'queue': waiting
        }
        
    except Exception as e:
        logger.exception(f"Error getting queue status: {e}")
        return {'total': 0, 'by_priority': {}}


def get_user_active_session(user_id):
    """
    Get user's active session (queued or in_progress)
    
    Args:
        user_id: User ID
        
    Returns:
        dict: Session info or None
    """
    try:
        sheet = get_worksheet(SHEET_TELECONSULT_SESSIONS)
        if not sheet:
            return None
        
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) <= 1:
            return None
        
        headers = all_values[0]
        
        # Search for active session (most recent)
        for i in range(len(all_values) - 1, 0, -1):
            row = all_values[i]
            if len(row) >= len(headers):
                record = dict(zip(headers, row))
                
                if (record.get('User_ID') == user_id and
                    record.get('Status') in ACTIVE_SESSION_STATUSES):
                    return record
        
        return None
        
    except Exception as e:
        logger.exception(f"Error getting active session: {e}")
        return None
