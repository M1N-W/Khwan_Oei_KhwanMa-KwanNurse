# -*- coding: utf-8 -*-
"""
Notification Service Module
Handles LINE push notifications
"""
import requests
from config import (
    get_logger,
    LINE_CHANNEL_ACCESS_TOKEN,
    NURSE_GROUP_ID,
    LINE_API_URL,
    WORKSHEET_LINK
)

logger = get_logger(__name__)


def send_line_push(message, target_id=None):
    """
    Send LINE push notification
    
    Args:
        message: Message text to send
        target_id: Target user/group ID (default: NURSE_GROUP_ID)
    
    Returns:
        boolean (success/failure)
    """
    try:
        access_token = LINE_CHANNEL_ACCESS_TOKEN
        if not target_id:
            target_id = NURSE_GROUP_ID
        
        if not access_token or not target_id:
            logger.warning("LINE token or target_id not configured")
            return False
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        
        payload = {
            "to": target_id,
            "messages": [{"type": "text", "text": message}]
        }
        
        resp = requests.post(LINE_API_URL, headers=headers, json=payload, timeout=8)
        
        if resp.status_code // 100 == 2:
            logger.info("Push notification sent to %s", target_id)
            return True
        else:
            logger.error("LINE push failed: %s %s", resp.status_code, resp.text)
            return False
    
    except Exception:
        logger.exception("Error sending LINE push notification")
        return False


def build_symptom_notification(user_id, pain, wound, fever, mobility, risk_level, risk_score):
    """
    Build notification message for symptom report
    Returns: formatted message string
    """
    message = (
        f"🚨 รายงานอาการเร่งด่วน!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User ID: {user_id}\n"
        f"⚠️ ความเสี่ยง: {risk_level}\n"
        f"📊 คะแนน: {risk_score}\n\n"
        f"📋 อาการ:\n"
        f"  • ความปวด: {pain}/10\n"
        f"  • แผล: {wound}\n"
        f"  • ไข้: {fever}\n"
        f"  • เคลื่อนไหว: {mobility}\n\n"
        f"⚡ กรุณาตรวจสอบทันที!\n"
        f"📊 ดูข้อมูล: {WORKSHEET_LINK}"
    )
    return message


def build_risk_notification(user_id, age, bmi, diseases_str, risk_level, risk_score):
    """
    Build notification message for risk assessment
    Returns: formatted message string
    """
    message = (
        f"🆕 ผู้ป่วยกลุ่มเสี่ยงสูง!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User ID: {user_id}\n"
        f"⚠️ ระดับ: {risk_level}\n"
        f"📊 คะแนน: {risk_score}\n\n"
        f"📋 ข้อมูล:\n"
        f"  • อายุ: {age} ปี\n"
        f"  • BMI: {bmi:.1f}\n"
        f"  • โรค: {diseases_str}\n\n"
        f"⚡ โปรดวางแผนติดตามใกล้ชิด\n"
        f"📊 ดูข้อมูล: {WORKSHEET_LINK}"
    )
    return message


def build_appointment_notification(user_id, name, phone, preferred_date, preferred_time, reason):
    """
    Build notification message for appointment request
    Returns: formatted message string
    """
    from datetime import datetime
    
    # Format date nicely
    try:
        date_obj = datetime.strptime(preferred_date, "%Y-%m-%d")
        thai_date = date_obj.strftime("%d/%m/%Y")
        day_name = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"][date_obj.weekday()]
        date_display = f"{day_name} {thai_date}"
    except:
        date_display = preferred_date
    
    message = (
        f"📅 การนัดหมายใหม่!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User ID: {user_id}\n"
    )
    
    if name:
        message += f"📝 ชื่อ: {name}\n"
    if phone:
        message += f"📞 เบอร์: {phone}\n"
    
    message += (
        f"📆 วัน: {date_display}\n"
        f"🕐 เวลา: {preferred_time} น.\n"
        f"💬 เรื่อง: {reason}\n\n"
        f"⚡ โปรดตรวจสอบและยืนยันนัด\n"
        f"📊 ดูรายละเอียด: {WORKSHEET_LINK}"
    )
    
    return message
