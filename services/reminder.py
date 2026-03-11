# -*- coding: utf-8 -*-
"""
Reminder Service Module
Handle follow-up reminder scheduling and sending
"""
from datetime import datetime, timedelta
from config import (
    LOCAL_TZ,
    REMINDER_INTERVALS,
    NURSE_GROUP_ID,
    get_logger
)
from database.reminders import (
    save_reminder_schedule,
    save_reminder_sent,
    save_reminder_response,
    get_pending_reminders,
    check_no_response_reminders
)
from services.notification import send_line_push

logger = get_logger(__name__)


def get_reminder_message(reminder_type):
    """
    Get the message template for each reminder type
    
    Args:
        reminder_type: Type of reminder
        
    Returns:
        str: Message text
    """
    messages = {
        'day3': (
            "👋 สวัสดีค่ะ\n\n"
            "📅 วันนี้เป็นวันที่ 3 หลังจำหน่ายแล้วค่ะ\n\n"
            "🩹 แผลหายดีไหมคะ?\n"
            "🌡️ มีไข้หรืออาการผิดปกติไหม?\n\n"
            "💬 กรุณารายงานอาการด้วยนะคะ\n"
            "พิมพ์: 'รายงานอาการ' เพื่อเริ่มบันทึก"
        ),
        'day7': (
            "📅 เตือนความจำค่ะ\n\n"
            "วันนี้เป็นสัปดาห์แรกหลังจำหน่ายแล้ว\n"
            "ถึงเวลานัดตรวจครั้งแรกค่ะ 🏥\n\n"
            "📋 สิ่งที่ควรเตรียม:\n"
            "• บัตรประชาชน\n"
            "• บัตรประกันสุขภาพ\n"
            "• ยาที่กำลังทาน\n\n"
            "💡 ต้องการนัดหมายใหม่ไหมคะ?\n"
            "พิมพ์: 'นัดหมาย' เพื่อจองเวลา"
        ),
        'day14': (
            "📅 สัปดาห์ที่ 2 หลังจำหน่าย\n\n"
            "🎯 เป้าหมายในช่วงนี้:\n"
            "• แผลควรหายดีแล้ว 80-90%\n"
            "• สามารถเคลื่อนไหวได้ปกติ\n"
            "• ลดการใช้ยาแก้ปวด\n\n"
            "❓ ความรู้สึกเป็นอย่างไรบ้างคะ?\n\n"
            "📝 พิมพ์: 'รายงานอาการ' เพื่ออัปเดต\n"
            "📚 พิมพ์: 'ความรู้' เพื่อดูคำแนะนำ"
        ),
        'day30': (
            "🎉 ครบ 1 เดือนแล้วค่ะ!\n\n"
            "👏 ยินดีด้วยที่ผ่านระยะพักฟื้นมาได้\n\n"
            "📊 ขอติดตามผลหน่อยนะคะ:\n"
            "• แผลหายสนิทแล้วหรือยัง?\n"
            "• กลับมาใช้ชีวิตได้ปกติไหม?\n"
            "• มีอาการผิดปกติหรือไม่?\n\n"
            "💬 กรุณาบอกเราหน่อยนะคะ\n\n"
            "🙏 ขอบคุณที่ให้เราดูแลค่ะ"
        )
    }
    
    return messages.get(reminder_type, "🔔 เตือนความจำ: กรุณาติดตามสุขภาพของคุณ")


def send_reminder(user_id, reminder_type):
    """
    Send a follow-up reminder to user
    
    Args:
        user_id: User ID to send to
        reminder_type: Type of reminder (day3, day7, day14, day30)
        
    Returns:
        bool: True if sent successfully
    """
    try:
        logger.info(f"Sending {reminder_type} reminder to {user_id}")
        
        # Get message
        message = get_reminder_message(reminder_type)
        
        # Send via LINE - FIXED: Correct parameter order (message, target_id)
        success = send_line_push(message, user_id)
        
        if success:
            # Record in database
            save_reminder_sent(user_id, reminder_type, message)
            logger.info(f"Successfully sent {reminder_type} reminder to {user_id}")
            return True
        else:
            logger.error(f"Failed to send {reminder_type} reminder to {user_id}")
            return False
            
    except Exception as e:
        logger.exception(f"Error sending reminder: {e}")
        return False


def schedule_follow_up_reminders(user_id, discharge_date):
    """
    Schedule all follow-up reminders for a patient
    
    Args:
        user_id: Patient's user ID
        discharge_date: Date patient was discharged (datetime or string)
        
    Returns:
        dict: Summary of scheduled reminders
    """
    try:
        logger.info(f"Scheduling follow-up reminders for user {user_id}")
        
        # Parse discharge date if string
        if isinstance(discharge_date, str):
            try:
                discharge_date = datetime.strptime(discharge_date, "%Y-%m-%d")
            except ValueError:
                discharge_date = datetime.strptime(discharge_date, "%Y-%m-%d %H:%M:%S")
        
        # Ensure timezone aware
        if discharge_date.tzinfo is None:
            discharge_date = discharge_date.replace(tzinfo=LOCAL_TZ)
        
        scheduled_count = 0
        scheduled_reminders = {}
        
        # Schedule each reminder type
        for reminder_type, config in REMINDER_INTERVALS.items():
            days = config['days']
            name = config['name']
            
            # Calculate scheduled date (at 9 AM)
            scheduled_date = discharge_date + timedelta(days=days)
            scheduled_date = scheduled_date.replace(hour=9, minute=0, second=0, microsecond=0)
            
            # Save to database
            success = save_reminder_schedule(
                user_id=user_id,
                discharge_date=discharge_date,
                reminder_type=reminder_type,
                scheduled_date=scheduled_date,
                notes=f"Auto-scheduled {name} reminder"
            )
            
            if success:
                scheduled_count += 1
                scheduled_reminders[reminder_type] = {
                    'name': name,
                    'scheduled_date': scheduled_date.strftime("%Y-%m-%d %H:%M")
                }
                from services.scheduler import schedule_reminder_job  # deferred to avoid circular import
                schedule_reminder_job(user_id, reminder_type, scheduled_date)
                logger.info(f"Scheduled {reminder_type} for {user_id} at {scheduled_date}")
            else:
                logger.error(f"Failed to schedule {reminder_type} for {user_id}")
        
        result = {
            'user_id': user_id,
            'discharge_date': discharge_date.strftime("%Y-%m-%d"),
            'scheduled_count': scheduled_count,
            'reminders': scheduled_reminders
        }
        
        logger.info(f"Successfully scheduled {scheduled_count} reminders for {user_id}")
        return result
        
    except Exception as e:
        logger.exception(f"Error scheduling follow-up reminders: {e}")
        return {
            'user_id': user_id,
            'error': str(e),
            'scheduled_count': 0
        }


def handle_reminder_response(user_id, response_text):
    """
    Handle user's response to a reminder
    
    Args:
        user_id: User ID
        response_text: User's response
        
    Returns:
        bool: True if handled successfully
    """
    try:
        logger.info(f"Handling reminder response from {user_id}")
        
        # Get pending reminders for this user
        pending = get_pending_reminders(user_id, None)
        
        if not pending:
            logger.warning(f"No pending reminders found for {user_id}")
            return False
        
        # Get the most recent pending reminder
        # Bug #7 fix: FollowUpReminders uses 'Timestamp', ReminderSchedules uses 'Created_At'
        # Sort using whichever field is present so we always pick the most recent one
        pending_sorted = sorted(
            pending,
            key=lambda x: x.get('Timestamp') or x.get('Created_At') or '',
            reverse=True
        )
        most_recent = pending_sorted[0]
        reminder_type = most_recent.get('Reminder_Type')
        
        # Save response
        success = save_reminder_response(user_id, reminder_type, response_text)
        
        if success:
            logger.info(f"Recorded response from {user_id} for {reminder_type}")
            
            # Analyze response for any concerns
            check_response_for_concerns(user_id, reminder_type, response_text)
            
            return True
        else:
            logger.error(f"Failed to save response from {user_id}")
            return False
            
    except Exception as e:
        logger.exception(f"Error handling reminder response: {e}")
        return False


def check_response_for_concerns(user_id, reminder_type, response_text):
    """
    Check if user's response contains concerning keywords
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder
        response_text: User's response
    """
    try:
        # Concerning keywords
        concern_keywords = [
            'ปวดมาก', 'ปวดเพิ่มขึ้น', 'หนอง', 'มีกลิ่น',
            'บวมแดง', 'มีไข้', 'ตัวร้อน', 'เจ็บมาก',
            'แผลแยก', 'เลือดออก', 'ไม่ดีขึ้น'
        ]
        
        response_lower = response_text.lower()
        
        has_concern = any(keyword in response_lower for keyword in concern_keywords)
        
        if has_concern:
            logger.warning(f"Concerning response detected from {user_id}: {response_text}")
            
            # Alert nurse - FIXED: Correct parameter order
            alert_message = (
                f"⚠️ แจ้งเตือนอาการน่ากังวล\n\n"
                f"👤 ผู้ป่วย: {user_id}\n"
                f"📋 Reminder: {reminder_type}\n"
                f"💬 Response: {response_text}\n\n"
                f"กรุณาติดตามด่วนค่ะ"
            )
            
            send_line_push(alert_message, NURSE_GROUP_ID)
            logger.info(f"Sent concern alert for {user_id} to nurse")
            
    except Exception as e:
        logger.exception(f"Error checking response for concerns: {e}")


def check_and_alert_no_response():
    """
    Check for reminders with no response and alert nurses
    
    Returns:
        int: Number of alerts sent
    """
    try:
        logger.info("Checking for reminders with no response")
        
        no_response_list = check_no_response_reminders()
        
        if not no_response_list:
            logger.info("No reminders found with missing responses")
            return 0
        
        logger.warning(f"Found {len(no_response_list)} reminders with no response")
        
        # Group by user for cleaner alerts
        users_no_response = {}
        for reminder in no_response_list:
            user_id = reminder.get('User_ID')
            if user_id not in users_no_response:
                users_no_response[user_id] = []
            users_no_response[user_id].append(reminder)
        
        # Send alerts - FIXED: Correct parameter order
        alerts_sent = 0
        for user_id, reminders in users_no_response.items():
            reminder_types = [r.get('Reminder_Type') for r in reminders]
            
            alert_message = (
                f"📢 แจ้งเตือนไม่มีการตอบกลับ\n\n"
                f"👤 ผู้ป่วย: {user_id}\n"
                f"📋 Reminders: {', '.join(reminder_types)}\n"
                f"⏰ เกิน 24 ชั่วโมงแล้ว\n\n"
                f"กรุณาติดตามผู้ป่วยค่ะ"
            )
            
            success = send_line_push(alert_message, NURSE_GROUP_ID)
            if success:
                alerts_sent += 1
                logger.info(f"Sent no-response alert for {user_id}")
        
        logger.info(f"Sent {alerts_sent} no-response alerts")
        return alerts_sent
        
    except Exception as e:
        logger.exception(f"Error checking and alerting no response: {e}")
        return 0


def get_reminder_summary(user_id):
    """
    Get summary of reminders for a user
    FIXED: Return correct field names matching webhook handler expectations
    
    Args:
        user_id: User ID
        
    Returns:
        dict: Summary of user's reminders with correct field names
    """
    try:
        from database.reminders import get_scheduled_reminders
        
        # Get all scheduled reminders
        all_scheduled = get_scheduled_reminders()
        user_scheduled = [r for r in all_scheduled if r.get('User_ID') == user_id]
        
        # Get pending (sent but not responded)
        pending = get_pending_reminders(user_id, None)
        
        # Count by status
        total_reminders = len(user_scheduled)
        responded = len([r for r in user_scheduled if r.get('Status') == 'responded'])
        pending_count = len([r for r in user_scheduled if r.get('Status') == 'sent'])
        no_response = len([r for r in user_scheduled if r.get('Status') == 'no_response'])
        
        # Get latest reminder
        latest = None
        if user_scheduled:
            # Sort by timestamp descending
            sorted_reminders = sorted(
                user_scheduled,
                key=lambda x: x.get('Created_At', ''),
                reverse=True
            )
            latest = sorted_reminders[0] if sorted_reminders else None
        
        summary = {
            'user_id': user_id,
            'total_reminders': total_reminders,
            'responded': responded,
            'pending': pending_count,
            'no_response': no_response,
            'latest': latest,
            'all_scheduled': user_scheduled,
            'pending_reminders': pending
        }
        
        return summary
        
    except Exception as e:
        logger.exception(f"Error getting reminder summary: {e}")
        return {
            'user_id': user_id,
            'error': str(e),
            'total_reminders': 0,
            'responded': 0,
            'pending': 0,
            'no_response': 0,
            'latest': None
        }
