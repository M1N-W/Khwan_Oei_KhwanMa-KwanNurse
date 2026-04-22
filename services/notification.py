# -*- coding: utf-8 -*-
"""
Notification Service Module
Handles LINE push notifications
"""
import time
import requests
from config import (
    get_logger,
    LINE_CHANNEL_ACCESS_TOKEN,
    NURSE_GROUP_ID,
    LINE_API_URL,
    WORKSHEET_LINK
)

logger = get_logger(__name__)

# Bounded retry policy for LINE push so transient network blips and 5xx
# responses do not immediately surface as a nurse alert failure, while still
# keeping the total request budget small enough not to blow webhook latency.
_LINE_PUSH_RETRIES = 2  # total attempts = 1 + retries
_LINE_PUSH_BACKOFF_SECONDS = (0.5, 1.0)
_LINE_PUSH_TIMEOUT_SECONDS = 6


def send_line_push(message, target_id=None):
    """
    Send LINE push notification with a short retry budget.

    Retries only on network errors, timeouts, and 5xx responses. 4xx responses
    are not retried because they indicate a caller/auth problem.

    Args:
        message: Message text to send
        target_id: Target user/group ID (default: NURSE_GROUP_ID)

    Returns:
        bool: success/failure
    """
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

    last_status = None
    for attempt in range(_LINE_PUSH_RETRIES + 1):
        try:
            resp = requests.post(
                LINE_API_URL,
                headers=headers,
                json=payload,
                timeout=_LINE_PUSH_TIMEOUT_SECONDS,
            )
            last_status = resp.status_code

            if resp.status_code // 100 == 2:
                if attempt > 0:
                    logger.info("Push notification sent to %s after %d retries", target_id, attempt)
                else:
                    logger.info("Push notification sent to %s", target_id)
                return True

            if resp.status_code // 100 == 5:
                # Transient server-side error: retry if budget remains
                logger.warning(
                    "LINE push 5xx (attempt %d/%d): %s %s",
                    attempt + 1, _LINE_PUSH_RETRIES + 1, resp.status_code, resp.text,
                )
            else:
                # 4xx (auth, bad payload): do NOT retry
                logger.error("LINE push failed (no retry): %s %s", resp.status_code, resp.text)
                return False

        except requests.exceptions.Timeout:
            logger.warning("LINE API timeout (attempt %d/%d)", attempt + 1, _LINE_PUSH_RETRIES + 1)
        except requests.exceptions.RequestException as e:
            logger.warning("LINE API request error (attempt %d/%d): %s", attempt + 1, _LINE_PUSH_RETRIES + 1, e)
        except Exception:
            logger.exception("Unexpected error sending LINE push notification")
            return False

        if attempt < _LINE_PUSH_RETRIES:
            time.sleep(_LINE_PUSH_BACKOFF_SECONDS[min(attempt, len(_LINE_PUSH_BACKOFF_SECONDS) - 1)])

    logger.error("LINE push giving up after %d attempts (last status=%s)",
                 _LINE_PUSH_RETRIES + 1, last_status)
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
