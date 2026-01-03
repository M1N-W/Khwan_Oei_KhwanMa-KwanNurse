# -*- coding: utf-8 -*-
"""
Appointment Service Module
Handles appointment booking and management
"""
from datetime import datetime
from config import get_logger
from database import save_appointment_data
from services.notification import send_line_push, build_appointment_notification

logger = get_logger(__name__)


def create_appointment(user_id, name, phone, preferred_date, preferred_time, reason):
    """
    Create new appointment request
    
    Args:
        user_id: User identifier
        name: Patient name (optional)
        phone: Phone number (optional)
        preferred_date: Date string (YYYY-MM-DD)
        preferred_time: Time string (HH:MM)
        reason: Reason for appointment
    
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Save to database
        success = save_appointment_data(
            user_id=user_id,
            name=name,
            phone=phone,
            preferred_date=preferred_date,
            preferred_time=preferred_time,
            reason=reason,
            status="New"
        )
        
        if not success:
            return False, "❌ เกิดปัญหาในการบันทึกนัดหมาย กรุณาลองใหม่อีกครั้งหรือติดต่อพยาบาลโดยตรงค่ะ"
        
        # Send notification to nurse
        notify_msg = build_appointment_notification(
            user_id, name, phone, preferred_date, preferred_time, reason
        )
        send_line_push(notify_msg)
        
        # Format confirmation message
        date_display = format_thai_date(preferred_date)
        
        confirm_msg = (
            f"✅ รับเรื่องการนัดหมายเรียบร้อยแล้วค่ะ\n\n"
            f"📅 วันที่: {date_display}\n"
            f"🕐 เวลา: {preferred_time} น.\n"
            f"💬 เรื่อง: {reason}\n\n"
            f"ทีมพยาบาลจะติดต่อกลับเพื่อยืนยันวันเวลาภายใน 24 ชั่วโมง\n"
            f"หากมีข้อสงสัยกรุณากดปุ่ม 'ปรึกษาพยาบาล' ค่ะ"
        )
        
        return True, confirm_msg
    
    except Exception:
        logger.exception("Error creating appointment")
        return False, "❌ เกิดข้อผิดพลาดในการบันทึกนัดหมาย กรุณาลองใหม่อีกครั้ง"


def format_thai_date(date_str):
    """
    Format date string to Thai format with day name
    
    Args:
        date_str: Date string (YYYY-MM-DD)
    
    Returns:
        str: Formatted date (e.g., "พุธ 15/01/2026")
    """
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        thai_date = date_obj.strftime("%d/%m/%Y")
        day_names = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
        day_name = day_names[date_obj.weekday()]
        return f"{day_name} {thai_date}"
    except:
        return date_str
