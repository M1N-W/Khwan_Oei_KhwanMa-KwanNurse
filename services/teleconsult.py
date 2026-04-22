# -*- coding: utf-8 -*-
"""
Teleconsult Service Module
Handle teleconsult logic, queue management, and nurse routing
"""
from datetime import datetime
from config import (
    LOCAL_TZ,
    OFFICE_HOURS,
    ISSUE_CATEGORIES,
    MAX_QUEUE_SIZE,
    NURSE_GROUP_ID,
    SessionStatus,
    get_logger,
)
from database.teleconsult import (
    create_session,
    add_to_queue,
    update_session_status,
    remove_from_queue,
    get_queue_status,
    get_user_active_session
)
from services.notification import send_line_push

logger = get_logger(__name__)


def is_office_hours():
    """
    Check if current time is within office hours
    
    Returns:
        bool: True if within office hours
    """
    try:
        now = datetime.now(tz=LOCAL_TZ)
        
        # Check if weekday
        if now.weekday() not in OFFICE_HOURS['weekdays']:
            return False
        
        # Check time
        start_time = datetime.strptime(OFFICE_HOURS['start'], "%H:%M").time()
        end_time = datetime.strptime(OFFICE_HOURS['end'], "%H:%M").time()
        current_time = now.time()
        
        return start_time <= current_time <= end_time
        
    except Exception as e:
        logger.exception(f"Error checking office hours: {e}")
        return False


def get_category_menu():
    """
    Get formatted category selection menu
    
    Returns:
        str: Formatted menu message
    """
    menu_items = []
    
    for i, (key, info) in enumerate(ISSUE_CATEGORIES.items(), 1):
        icon = info['icon']
        name = info['name_th']
        menu_items.append(f"{i}. {icon} {name}")
    
    menu = "📋 เลือกเรื่องที่ต้องการปรึกษา:\n\n" + "\n".join(menu_items)
    menu += "\n\nพิมพ์หมายเลข (1-5) เพื่อเลือก"
    
    return menu


def parse_category_choice(choice_text):
    """
    Parse user's category choice
    
    Args:
        choice_text: User's input (number or text)
        
    Returns:
        str: Category key or None
    """
    try:
        # Try as number (only pure digit strings)
        stripped = choice_text.strip()
        if stripped.isdigit():
            choice_num = int(stripped)
            categories = list(ISSUE_CATEGORIES.keys())
            if 1 <= choice_num <= len(categories):
                return categories[choice_num - 1]
        
        # Try as text matching
        choice_lower = choice_text.lower().strip()
        for key, info in ISSUE_CATEGORIES.items():
            if (key in choice_lower or 
                info['name_th'] in choice_text or
                info['icon'] in choice_text):
                return key
        
        return None
        
    except Exception as e:
        logger.exception(f"Error parsing category: {e}")
        return None


def start_teleconsult(user_id, issue_type, description=""):
    """
    Start a teleconsult session
    
    Args:
        user_id: Patient's LINE user ID
        issue_type: Issue category
        description: User's description
        
    Returns:
        dict: Response message and session info
    """
    try:
        logger.info(f"Starting teleconsult for {user_id}, type: {issue_type}")
        
        # Check if user already has active session
        existing_session = get_user_active_session(user_id)
        if existing_session:
            queue_pos = existing_session.get('Queue_Position', '?')
            return {
                'success': False,
                'message': (
                    f"⚠️ คุณมีคำขอปรึกษาที่กำลังดำเนินการอยู่แล้วค่ะ\n\n"
                    f"📊 ตำแหน่งในคิว: {queue_pos}\n"
                    f"📋 ประเภท: {existing_session.get('Issue_Type')}\n\n"
                    f"กรุณารอพยาบาลติดต่อกลับนะคะ\n"
                    f"หรือพิมพ์ 'ยกเลิก' เพื่อยกเลิกคำขอเดิม"
                )
            }
        
        # Get category info
        category_info = ISSUE_CATEGORIES.get(issue_type, ISSUE_CATEGORIES['other'])
        priority = category_info['priority']
        icon = category_info['icon']
        name_th = category_info['name_th']
        max_wait = category_info['max_wait_minutes']
        
        # Check if emergency
        if issue_type == 'emergency':
            return handle_emergency(user_id, description)
        
        # Check office hours for non-emergency
        if not is_office_hours():
            return handle_after_hours(user_id, issue_type, description)
        
        # Check queue size
        queue_status = get_queue_status()
        if queue_status['total'] >= MAX_QUEUE_SIZE:
            return {
                'success': False,
                'message': (
                    "😔 ขออภัยค่ะ\n\n"
                    "ขณะนี้คิวเต็มแล้ว\n"
                    "กรุณาลองใหม่อีกครั้งในอีก 15-30 นาที\n\n"
                    "หรือหากเป็นเรื่องฉุกเฉิน\n"
                    "โปรดโทร 1669 ทันทีค่ะ"
                )
            }
        
        # Create session
        session = create_session(user_id, issue_type, priority, description)
        if not session:
            return {
                'success': False,
                'message': "เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง"
            }
        
        # Add to queue
        queue_info = add_to_queue(
            session['session_id'],
            user_id,
            issue_type,
            priority
        )

        if not queue_info:
            update_session_status(
                session['session_id'],
                SessionStatus.QUEUE_FAILED,
                notes='Queue insertion failed'
            )
            return {
                'success': False,
                'message': "เกิดข้อผิดพลาดในการเข้าคิว กรุณาลองใหม่"
            }
        
        # Alert nurse
        alert_nurse_new_request(session, queue_info)
        
        # Build response message
        wait_time = f"{max_wait}-{max_wait + 10}" if queue_info['position'] == 1 else f"{queue_info['estimated_wait']}"
        
        message = (
            f"✅ รับเรื่องแล้วค่ะ\n\n"
            f"📋 ประเภท: {icon} {name_th}\n"
            f"📊 ตำแหน่งในคิว: {queue_info['position']}\n"
            f"⏱️ เวลารอโดยประมาณ: {wait_time} นาที\n\n"
            f"พยาบาลจะติดต่อกลับโดยเร็วนะคะ 💚\n\n"
            f"💡 พิมพ์ 'ยกเลิก' ถ้าต้องการยกเลิกคำขอ"
        )
        
        return {
            'success': True,
            'message': message,
            'session': session,
            'queue': queue_info
        }
        
    except Exception as e:
        logger.exception(f"Error starting teleconsult: {e}")
        return {
            'success': False,
            'message': "เกิดข้อผิดพลาด กรุณาลองใหม่ภายหลัง"
        }


def handle_emergency(user_id, description):
    """
    Handle emergency consultation request
    
    Args:
        user_id: Patient ID
        description: Emergency description
        
    Returns:
        dict: Response
    """
    try:
        logger.warning(f"EMERGENCY request from {user_id}: {description}")
        
        # Create high-priority session
        session = create_session(user_id, 'emergency', 1, description)
        
        if not session:
            return {
                'success': False,
                'message': "เกิดข้อผิดพลาด กรุณาโทร 1669 ทันที"
            }
        
        # Update status to in_progress (skip queue)
        update_session_status(session['session_id'], SessionStatus.IN_PROGRESS)
        
        # Send URGENT alert to nurse
        alert_message = (
            f"🚨🚨 เรื่องฉุกเฉิน 🚨🚨\n\n"
            f"👤 ผู้ป่วย: {user_id}\n"
            f"💬 อาการ: {description or '(ไม่ระบุ)'}\n"
            f"🕐 เวลา: {datetime.now(tz=LOCAL_TZ).strftime('%H:%M น.')}\n\n"
            f"⚠️ กรุณาติดต่อกลับภายใน 5 นาที\n"
            f"Session ID: {session['session_id']}"
        )
        
        send_line_push(alert_message, NURSE_GROUP_ID)
        
        message = (
            "🚨 รับเรื่องฉุกเฉินแล้วค่ะ\n\n"
            "📞 กำลังติดต่อพยาบาลด่วน...\n\n"
            "⚠️ ถ้าอาการรุนแรงมาก\n"
            "โปรดโทร 1669 ทันทีค่ะ\n\n"
            "พยาบาลจะติดต่อกลับภายใน 5 นาที"
        )
        
        return {
            'success': True,
            'message': message,
            'session': session,
            'is_emergency': True
        }
        
    except Exception as e:
        logger.exception(f"Error handling emergency: {e}")
        return {
            'success': False,
            'message': "เกิดข้อผิดพลาด กรุณาโทร 1669 ทันที"
        }


def handle_after_hours(user_id, issue_type, description):
    """
    Handle request made outside office hours.

    FIXED (Bug #2): ตอนนี้บันทึก session แบบ after_hours_pending ลง DB ทันที
    เพื่อป้องกันคำขอสูญหายเมื่อผู้ใช้แจ้งนอกเวลาทำการ
    
    Args:
        user_id: Patient ID
        issue_type: Issue category
        description: Description
        
    Returns:
        dict: Response
    """
    try:
        now = datetime.now(tz=LOCAL_TZ)
        current_time = now.strftime("%H:%M")

        # --- FIX: บันทึก session ก่อนเพื่อป้องกันข้อมูลสูญหาย ---
        session = create_session(user_id, issue_type, priority=3, description=description)
        if session:
            update_session_status(session['session_id'], SessionStatus.AFTER_HOURS_PENDING)
            logger.info(
                f"After-hours session saved: {session['session_id']} for {user_id}"
            )
        else:
            logger.error(f"Failed to save after-hours session for {user_id}")
        # ----------------------------------------------------------

        message = (
            f"สวัสดีค่ะ 😊\n\n"
            f"⏰ ขณะนี้นอกเวลาทำการ (เวลา {current_time} น.)\n"
            f"🕐 เวลาทำการ: {OFFICE_HOURS['start']}-{OFFICE_HOURS['end']} น. (จันทร์-ศุกร์)\n\n"
            f"📌 คำถามของคุณสำคัญมากไหมคะ?\n\n"
            f"1. 🚨 ฉุกเฉิน (ติดต่อเจ้าหน้าที่เวร)\n"
            f"2. 📝 ไม่เร่งด่วน (บันทึกไว้ติดต่อพรุ่งนี้)\n\n"
            f"พิมพ์หมายเลข 1 หรือ 2"
        )

        return {
            'success': True,
            'message': message,
            'is_after_hours': True,
            'awaiting_choice': True,
            'session': session
        }

    except Exception as e:
        logger.exception(f"Error handling after hours: {e}")
        return {
            'success': False,
            'message': "เกิดข้อผิดพลาด กรุณาลองใหม่"
        }


def handle_after_hours_choice(user_id, choice_text):
    """
    Process the user's answer (1 or 2) after receiving the after-hours menu.

    ADDED (Bug #2 fix): จัดการคำตอบของผู้ใช้ที่แจ้งนอกเวลาทำการ
    - เลือก 1 → escalate เป็น emergency
    - เลือก 2 → ยืนยันบันทึกและแจ้งพยาบาล

    Args:
        user_id: Patient ID
        choice_text: "1" or "2" (or Thai equivalent)

    Returns:
        dict: Response with 'message' key
    """
    try:
        stripped = str(choice_text).strip()

        if stripped == "1" or "ฉุกเฉิน" in stripped:
            # Escalate — ดึง pending session แล้ว escalate
            session = get_user_active_session(user_id)
            description = session.get('Description', '') if session else ''
            return handle_emergency(user_id, description)

        elif stripped == "2" or "ไม่เร่งด่วน" in stripped or "บันทึก" in stripped:
            # ยืนยันว่าบันทึกแล้ว และแจ้งพยาบาล
            session = get_user_active_session(user_id)
            if session:
                nurse_alert = (
                    f"📋 มีคำขอนอกเวลาทำการ (ไม่เร่งด่วน)\n\n"
                    f"👤 ผู้ป่วย: {user_id}\n"
                    f"📂 ประเภท: {session.get('Issue_Type', '-')}\n"
                    f"💬 รายละเอียด: {session.get('Description', '(ไม่มี)')}\n\n"
                    f"⏰ กรุณาติดต่อกลับในวันทำการถัดไปค่ะ"
                )
                send_line_push(nurse_alert, NURSE_GROUP_ID)
                logger.info(f"After-hours non-urgent request logged and nurse notified for {user_id}")

            return {
                'success': True,
                'message': (
                    "✅ บันทึกคำขอของคุณเรียบร้อยแล้วค่ะ\n\n"
                    "📋 ทีมพยาบาลจะติดต่อกลับในวันทำการถัดไป\n"
                    f"🕐 เวลาทำการ: {OFFICE_HOURS['start']}-{OFFICE_HOURS['end']} น.\n\n"
                    "หากมีอาการฉุกเฉิน กรุณาโทร 1669 ทันทีค่ะ"
                )
            }

        else:
            # ตอบไม่ตรง ให้แสดง menu ซ้ำ
            return {
                'success': True,
                'message': (
                    "กรุณาพิมพ์หมายเลข 1 หรือ 2 ค่ะ\n\n"
                    "1. 🚨 ฉุกเฉิน\n"
                    "2. 📝 ไม่เร่งด่วน"
                )
            }

    except Exception as e:
        logger.exception(f"Error handling after-hours choice: {e}")
        return {
            'success': False,
            'message': "เกิดข้อผิดพลาด กรุณาลองใหม่"
        }


def cancel_consultation(user_id):
    """
    Cancel user's active consultation
    
    Args:
        user_id: Patient ID
        
    Returns:
        dict: Response
    """
    try:
        session = get_user_active_session(user_id)
        
        if not session:
            return {
                'success': False,
                'message': "ไม่พบคำขอปรึกษาที่กำลังดำเนินการค่ะ"
            }
        
        session_id = session.get('Session_ID')
        
        # Update session status
        update_session_status(session_id, SessionStatus.CANCELLED, notes='Cancelled by user')
        
        # Remove from queue
        remove_from_queue(session_id)
        
        logger.info(f"Cancelled session {session_id} for user {user_id}")
        
        return {
            'success': True,
            'message': (
                "✅ ยกเลิกคำขอแล้วค่ะ\n\n"
                "หากต้องการปรึกษาอีกครั้ง\n"
                "สามารถเลือก 'ปรึกษาพยาบาล' ใหม่ได้เลยค่ะ"
            )
        }
        
    except Exception as e:
        logger.exception(f"Error cancelling consultation: {e}")
        return {
            'success': False,
            'message': "เกิดข้อผิดพลาดในการยกเลิก กรุณาลองใหม่"
        }


def alert_nurse_new_request(session, queue_info):
    """
    Send alert to nurse about new consultation request
    
    Args:
        session: Session info
        queue_info: Queue info
    """
    try:
        issue_type = session['issue_type']
        category_info = ISSUE_CATEGORIES.get(issue_type, {})
        icon = category_info.get('icon', '❓')
        name_th = category_info.get('name_th', 'อื่นๆ')
        priority_text = {1: 'สูง', 2: 'กลาง', 3: 'ต่ำ'}.get(session['priority'], 'กลาง')
        
        queue_status = get_queue_status()
        
        message = (
            f"🔔 คำขอปรึกษาใหม่\n\n"
            f"👤 ผู้ป่วย: {session['user_id']}\n"
            f"📋 ประเภท: {icon} {name_th}\n"
            f"⚠️ ระดับ: {priority_text}\n"
            f"💬 รายละเอียด: {session.get('description', '(ไม่มี)')}\n\n"
            f"📊 คิวปัจจุบัน: {queue_status['total']} คน\n"
            f"⏱️ เวลารอ: {queue_info.get('estimated_wait', '?')} นาที\n\n"
            f"Session ID: {session['session_id']}"
        )
        
        send_line_push(message, NURSE_GROUP_ID)
        
        logger.info(f"Sent nurse alert for session {session['session_id']}")
        
    except Exception as e:
        logger.exception(f"Error sending nurse alert: {e}")


def get_queue_info_message():
    """
    Get current queue information message
    
    Returns:
        str: Formatted queue info
    """
    try:
        queue_status = get_queue_status()
        
        if queue_status['total'] == 0:
            return "📊 ขณะนี้ไม่มีคิวรอค่ะ"
        
        by_priority = queue_status['by_priority']
        
        message = (
            f"📊 สถานะคิวปัจจุบัน\n\n"
            f"รวมทั้งหมด: {queue_status['total']} คน\n\n"
            f"🚨 ฉุกเฉิน: {by_priority.get(1, 0)} คน\n"
            f"⚠️ กลาง: {by_priority.get(2, 0)} คน\n"
            f"📝 ต่ำ: {by_priority.get(3, 0)} คน"
        )
        
        return message
        
    except Exception as e:
        logger.exception(f"Error getting queue info: {e}")
        return "ไม่สามารถดูสถานะคิวได้ในขณะนี้"
