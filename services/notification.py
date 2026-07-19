# -*- coding: utf-8 -*-
"""
Notification Service Module
Handles LINE push notifications
"""
import time
import requests
from typing import Optional
from config import (
    get_logger,
    LINE_CHANNEL_ACCESS_TOKEN,
    NURSE_GROUP_ID,
    LINE_API_URL,
    LINE_CONTENT_API_URL,
    LINE_REPLY_API_URL,
    WORKSHEET_LINK,
)

from services.metrics import incr as _metric

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
        _metric("line_push.skip_unconfigured")
        return False

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    if isinstance(message, dict):
        line_messages = [message]
    elif isinstance(message, list):
        line_messages = message
    else:
        line_messages = [{"type": "text", "text": str(message)}]

    payload = {
        "to": target_id,
        "messages": line_messages
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
                    _metric("line_push.success_after_retry")
                else:
                    logger.info("Push notification sent to %s", target_id)
                _metric("line_push.success")
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
                _metric("line_push.4xx")
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
    _metric("line_push.gave_up")
    return False


def send_line_push_objects(messages: list, target_id: str) -> bool:
    """
    Send a list of LINE message objects (Text, Flex, Quick Reply, etc.)
    via the push API (KWN-05).

    Unlike ``send_line_push`` which accepts a plain string, this function
    accepts pre-built LINE message dicts so callers can send Flex / Quick
    Reply payloads.

    Args:
        messages:  List of LINE message object dicts.
        target_id: Target LINE user/group ID.

    Returns:
        bool: True on success.
    """
    access_token = LINE_CHANNEL_ACCESS_TOKEN
    if not access_token or not target_id or not messages:
        logger.warning("send_line_push_objects: token, target_id, or messages missing")
        _metric("line_push_objects.skip_unconfigured")
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    payload = {"to": target_id, "messages": messages}

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
                _metric("line_push_objects.success")
                logger.info("Rich push sent to %s (%d objects)", target_id, len(messages))
                return True
            if resp.status_code // 100 == 5:
                logger.warning(
                    "line_push_objects 5xx attempt %d/%d: %s",
                    attempt + 1, _LINE_PUSH_RETRIES + 1, resp.status_code,
                )
            else:
                logger.error("line_push_objects 4xx (no retry): %s %s", resp.status_code, resp.text)
                _metric("line_push_objects.4xx")
                return False
        except requests.exceptions.Timeout:
            logger.warning("line_push_objects timeout attempt %d/%d", attempt + 1, _LINE_PUSH_RETRIES + 1)
        except requests.exceptions.RequestException as e:
            logger.warning("line_push_objects network error attempt %d/%d: %s", attempt + 1, _LINE_PUSH_RETRIES + 1, e)
        except Exception:
            logger.exception("line_push_objects unexpected error")
            return False

        if attempt < _LINE_PUSH_RETRIES:
            time.sleep(_LINE_PUSH_BACKOFF_SECONDS[min(attempt, len(_LINE_PUSH_BACKOFF_SECONDS) - 1)])

    logger.error("line_push_objects giving up after %d attempts (last status=%s)", _LINE_PUSH_RETRIES + 1, last_status)
    _metric("line_push_objects.gave_up")
    return False


def _get_patient_prefix_label(user_id: str) -> str:
    """Helper to return Name Surname (HN: xxx) or fallback to User ID if not registered."""
    if not user_id:
        return "-"
    try:
        from database.patient_profile import read_patient_profile
        profile = read_patient_profile(user_id)
        if profile:
            first = (profile.get("first_name") or "").strip()
            last = (profile.get("last_name") or "").strip()
            hn = (profile.get("hn") or "").strip()
            name = f"{first} {last}".strip()
            if last:
                double_suffix = f"{last} {last}"
                while name.endswith(double_suffix):
                    name = name[:-len(last)].strip()
            if name:
                label = name
                if hn:
                    label += f" (HN: {hn})"
                return label
    except Exception:
        logger.exception("Failed to retrieve profile for prefix label user_id=%s", user_id)
    return user_id


def build_symptom_notification(user_id, pain, wound, fever, mobility, risk_level, risk_score):
    """
    Build notification message for symptom report
    Returns: formatted message string
    """
    patient_label = _get_patient_prefix_label(user_id)
    message = (
        f"🚨 รายงานอาการเร่งด่วน!\n"
        f"───────────────\n"
        f"👤 ผู้ป่วย: {patient_label}\n"
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
    patient_label = _get_patient_prefix_label(user_id)
    message = (
        f"🆕 ผู้ป่วยกลุ่มเสี่ยงสูง!\n"
        f"───────────────\n"
        f"👤 ผู้ป่วย: {patient_label}\n"
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


# ---------------------------------------------------------------------------
# Wound image flow helpers (S2-2)
# ---------------------------------------------------------------------------
_LINE_CONTENT_TIMEOUT_SECONDS = 10
_LINE_REPLY_TIMEOUT_SECONDS = 6


def download_line_content(message_id: str) -> Optional[bytes]:
    """
    Download an image (or other binary content) from LINE Content API.

    Used by the wound-image flow to fetch bytes for Gemini Vision analysis.
    LINE retains content for ~7 days.

    Args:
        message_id: ``message.id`` from a LINE webhook image event.

    Returns:
        Raw bytes on 2xx, or None on auth/network/4xx/5xx error.
    """
    if not message_id:
        return None
    if not LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("LINE token not configured — cannot download content %s", message_id)
        _metric("line_content.skip_unconfigured")
        return None

    url = f"{LINE_CONTENT_API_URL}/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}

    try:
        resp = requests.get(url, headers=headers, timeout=_LINE_CONTENT_TIMEOUT_SECONDS)
        if resp.status_code // 100 == 2:
            logger.info("line_content downloaded message_id=%s bytes=%d",
                        message_id, len(resp.content))
            _metric("line_content.success")
            return resp.content
        logger.warning("line_content non-2xx status=%s message_id=%s",
                       resp.status_code, message_id)
        _metric(f"line_content.status_{resp.status_code // 100}xx")
        return None
    except requests.exceptions.Timeout:
        logger.warning("line_content timeout message_id=%s", message_id)
        _metric("line_content.timeout")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning("line_content network error message_id=%s err=%s", message_id, e)
        _metric("line_content.network_error")
        return None
    except Exception:
        logger.exception("line_content unexpected error message_id=%s", message_id)
        return None


def reply_line_message(reply_token: str, message: str) -> bool:
    """
    Send a single text reply via LINE Reply API.

    Reply API is preferred over push for in-conversation responses because:
    - It's free of LINE quota cost (push is metered).
    - Reply tokens expire ~30s, so this should be called from the webhook
      handler thread synchronously.

    Args:
        reply_token: ``event.replyToken`` from the LINE webhook.
        message: Plain text to send back to the user.

    Returns:
        bool: True on 2xx, False otherwise.
    """
    if not reply_token or not message:
        return False
    if not LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("LINE token not configured — cannot reply")
        _metric("line_reply.skip_unconfigured")
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": message[:5000]}],  # LINE caps 5000 chars/text
    }
    try:
        resp = requests.post(
            LINE_REPLY_API_URL,
            headers=headers,
            json=payload,
            timeout=_LINE_REPLY_TIMEOUT_SECONDS,
        )
        if resp.status_code // 100 == 2:
            _metric("line_reply.success")
            return True
        logger.warning("line_reply non-2xx status=%s body=%s",
                       resp.status_code, resp.text[:200])
        _metric(f"line_reply.status_{resp.status_code // 100}xx")
        return False
    except requests.exceptions.Timeout:
        _metric("line_reply.timeout")
        return False
    except requests.exceptions.RequestException as e:
        logger.warning("line_reply network error: %s", e)
        _metric("line_reply.network_error")
        return False
    except Exception:
        logger.exception("line_reply unexpected error")
        return False


def reply_line_message_objects(reply_token: str, messages: list) -> bool:
    """
    Send a list of LINE message objects via reply API (KWN-05).

    Args:
        reply_token: ``event.replyToken`` from the LINE webhook.
        messages:    List of LINE message object dicts.

    Returns:
        bool: True on success.
    """
    if not reply_token or not messages:
        return False
    if not LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("LINE token not configured — cannot reply with objects")
        _metric("line_reply_objects.skip_unconfigured")
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"replyToken": reply_token, "messages": messages}
    try:
        resp = requests.post(
            LINE_REPLY_API_URL,
            headers=headers,
            json=payload,
            timeout=_LINE_REPLY_TIMEOUT_SECONDS,
        )
        if resp.status_code // 100 == 2:
            _metric("line_reply_objects.success")
            return True
        logger.warning("line_reply_objects non-2xx status=%s body=%s", resp.status_code, resp.text[:200])
        _metric(f"line_reply_objects.status_{resp.status_code // 100}xx")
        return False
    except requests.exceptions.Timeout:
        _metric("line_reply_objects.timeout")
        return False
    except requests.exceptions.RequestException as e:
        logger.warning("line_reply_objects network error: %s", e)
        _metric("line_reply_objects.network_error")
        return False
    except Exception:
        logger.exception("line_reply_objects unexpected error")
        return False


def build_wound_alert_message(
    user_id: str,
    severity: str,
    observations: list,
    advice: str,
    confidence: float,
) -> str:
    """
    Build the nurse-facing alert message for a wound image with high or
    medium severity.
    """
    severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "🟡")
    severity_label = {"high": "สูง", "medium": "ปานกลาง", "low": "ต่ำ"}.get(severity, severity)

    obs_lines = "\n".join(f"  • {o}" for o in (observations or [])[:4]) or "  • (ไม่มีข้อสังเกตเพิ่มเติม)"
    confidence_pct = int(round(confidence * 100))

    patient_label = _get_patient_prefix_label(user_id)
    return (
        "📸 ภาพแผลผู้ป่วยใหม่!\n"
        "───────────────\n"
        f"👤 ผู้ป่วย: {patient_label}\n"
        f"{severity_emoji} ความรุนแรง: {severity_label}\n"
        f"🎯 ความมั่นใจ AI: {confidence_pct}%\n\n"
        "📋 ข้อสังเกต:\n"
        f"{obs_lines}\n\n"
        f"💡 คำแนะนำเบื้องต้น: {advice or '-'}\n\n"
        "⚡ กรุณาตรวจสอบรูปต้นฉบับใน LINE\n"
        f"📊 ดูข้อมูล: {WORKSHEET_LINK}"
    )


def build_wound_user_reply(severity: str, observations: list, advice: str) -> str:
    """
    Build the patient-facing reply message after analyzing their wound image.
    """
    severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "🟡")
    severity_label = {"high": "สูง", "medium": "ปานกลาง", "low": "ต่ำ"}.get(severity, "ปานกลาง")

    obs_text = ""
    if observations:
        obs_text = "\nสิ่งที่ AI สังเกตเห็น:\n" + "\n".join(f"• {o}" for o in observations[:3])

    closing = (
        "\n\n⚠️ ผลจาก AI เป็นการประเมินเบื้องต้นเท่านั้น พยาบาลจะตรวจสอบและติดต่อกลับ"
        if severity in ("high", "medium")
        else "\n\nหากมีอาการผิดปกติเพิ่มเติม กรุณาติดต่อพยาบาล"
    )

    return (
        f"{severity_emoji} ได้รับรูปแล้ว\n"
        f"ระดับ: {severity_label}\n"
        f"{obs_text}\n\n"
        f"💡 {advice or '-'}"
        f"{closing}"
    )


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
    
    patient_label = _get_patient_prefix_label(user_id)
    message = (
        f"📅 การนัดหมายใหม่!\n"
        f"───────────────\n"
        f"👤 ผู้ป่วย: {patient_label}\n"
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


def build_emergency_text_alert(user_id: str, description: str) -> str:
    """Build a clean text alert message for emergencies (when Flex is disabled)."""
    try:
        from database.patient_profile import read_patient_profile
        profile = read_patient_profile(user_id) or {}
    except Exception:
        profile = {}

    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    hn = (profile.get("hn") or "").strip()

    name = f"{first} {last}".strip()
    if last:
        double_suffix = f"{last} {last}"
        while name.endswith(double_suffix):
            name = name[:-len(last)].strip()
    if not name:
        name = "ไม่ระบุชื่อ"
    hn_str = f" (HN: {hn})" if hn else ""

    desc = description.strip() if description and description.strip() else "แจ้งเหตุฉุกเฉิน (นอกเวลาทำการ)"
    from datetime import datetime
    from config import LOCAL_TZ
    time_str = datetime.now(tz=LOCAL_TZ).strftime("%H:%M น.")

    return (
        f"🚨🚨 เรื่องฉุกเฉิน 🚨🚨\n\n"
        f"👤 ผู้ป่วย: {name}{hn_str}\n"
        f"💬 อาการ: {desc}\n"
        f"🕐 เวลา: {time_str}\n\n"
        f"⚠️ กรุณาติดต่อกลับภายใน 5 นาที"
    )


def build_emergency_flex_alert(user_id: str, description: str) -> dict:
    """Build a beautiful Flex message alert for emergencies."""
    try:
        from database.patient_profile import read_patient_profile
        profile = read_patient_profile(user_id) or {}
    except Exception:
        profile = {}

    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    hn = (profile.get("hn") or "").strip()
    phone = (profile.get("phone") or "").strip()

    name = f"{first} {last}".strip()
    if last:
        double_suffix = f"{last} {last}"
        while name.endswith(double_suffix):
            name = name[:-len(last)].strip()
    if not name:
        name = "ไม่ระบุชื่อ"
    hn_str = hn or "ไม่ระบุ"

    desc = description.strip() if description and description.strip() else "แจ้งเหตุฉุกเฉิน (นอกเวลาทำการ)"
    
    from datetime import datetime
    from config import LOCAL_TZ
    time_str = datetime.now(tz=LOCAL_TZ).strftime("%H:%M")

    # Retrieve briefing data
    try:
        from services.presession import build_pre_consult_briefing_data
        briefing = build_pre_consult_briefing_data(user_id, 'emergency', description)
    except Exception:
        briefing = {"risk": "low", "summary": "", "questions": []}

    risk = briefing.get("risk", "low")
    summary = briefing.get("summary") or ""
    questions = briefing.get("questions") or []

    risk_color = {"high": "#DC3545", "medium": "#FF8C00", "low": "#28A745"}.get(risk, "#28A745")
    risk_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(risk, "🟢")

    header_contents = [
        {
            "type": "text",
            "text": "🚨 แจ้งเรื่องฉุกเฉิน 🚨",
            "weight": "bold",
            "size": "lg",
            "color": "#FFFFFF",
            "align": "center"
        }
    ]

    body_contents = [
        {
            "type": "text",
            "text": f"👤 ผู้ป่วย: {name}",
            "weight": "bold",
            "size": "md",
            "wrap": True
        },
        {
            "type": "text",
            "text": f"🏥 HN: {hn_str}",
            "size": "sm",
            "color": "#555555"
        },
        {
            "type": "text",
            "text": f"💬 อาการ: {desc}",
            "size": "md",
            "wrap": True,
            "margin": "md"
        },
        {
            "type": "text",
            "text": f"⏰ เวลา: {time_str} น.",
            "size": "xs",
            "color": "#888888",
            "margin": "sm"
        },
        {
            "type": "separator",
            "margin": "lg"
        },
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": "ระดับความเสี่ยง (AI):",
                    "size": "sm",
                    "color": "#555555"
                },
                {
                    "type": "text",
                    "text": f"{risk_emoji} {risk.upper()}",
                    "weight": "bold",
                    "size": "sm",
                    "color": risk_color,
                    "align": "end"
                }
            ]
        }
    ]

    if summary:
        body_contents.append({
            "type": "text",
            "text": "📝 สรุปอาการ (AI):",
            "weight": "bold",
            "size": "sm",
            "color": "#007BFF",
            "margin": "lg"
        })
        body_contents.append({
            "type": "text",
            "text": summary,
            "size": "sm",
            "wrap": True,
            "color": "#333333"
        })

    if questions:
        body_contents.append({
            "type": "text",
            "text": "❓ คำถามแนะนำที่ควรโทรสัมภาษณ์:",
            "weight": "bold",
            "size": "sm",
            "color": "#007BFF",
            "margin": "lg"
        })
        for i, q in enumerate(questions, 1):
            body_contents.append({
                "type": "text",
                "text": f"{i}. {q}",
                "size": "sm",
                "wrap": True,
                "color": "#333333",
                "margin": "xs"
            })

    footer_contents = []
    if phone:
        footer_contents.append({
            "type": "button",
            "action": {
                "type": "uri",
                "label": "📞 โทรติดต่อคนไข้",
                "uri": f"tel:{phone}"
            },
            "style": "primary",
            "color": "#DC3545"
        })
    if WORKSHEET_LINK:
        footer_contents.append({
            "type": "button",
            "action": {
                "type": "uri",
                "label": "📊 เปิดฐานข้อมูลคนไข้",
                "uri": WORKSHEET_LINK
            },
            "style": "secondary"
        })

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#DC3545",
            "contents": header_contents
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents
        }
    }
    if footer_contents:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents
        }

    return {
        "type": "flex",
        "altText": f"🚨 ฉุกเฉิน: {name}",
        "contents": bubble
    }


def build_clinical_alert(alert_type: str, user_id: str, context: dict) -> str | dict:
    """
    Unified router for building clinical alert notifications (KWN-09).
    Bridges to individual notification builder functions.
    """
    if alert_type == "symptom":
        return build_symptom_notification(
            user_id=user_id,
            pain=context.get("pain", "-"),
            wound=context.get("wound", "-"),
            fever=context.get("fever", "-"),
            mobility=context.get("mobility", "-"),
            risk_level=context.get("risk_level", "-"),
            risk_score=context.get("risk_score", 0)
        )
    elif alert_type == "risk":
        return build_risk_notification(
            user_id=user_id,
            age=context.get("age", 0),
            bmi=context.get("bmi", 0.0),
            diseases_str=context.get("diseases_str", "-"),
            risk_level=context.get("risk_level", "-"),
            risk_score=context.get("risk_score", 0)
        )
    elif alert_type == "appointment":
        return build_appointment_notification(
            user_id=user_id,
            name=context.get("name", "-"),
            phone=context.get("phone", "-"),
            preferred_date=context.get("preferred_date", "-"),
            preferred_time=context.get("preferred_time", "-"),
            reason=context.get("reason", "-")
        )
    elif alert_type == "wound":
        return build_wound_alert_message(
            user_id=user_id,
            severity=context.get("severity", "medium"),
            observations=context.get("observations", []),
            advice=context.get("advice", ""),
            confidence=context.get("confidence", 0.0)
        )
    elif alert_type == "emergency":
        from config import ENABLE_RICH_MESSAGES
        if ENABLE_RICH_MESSAGES:
            return build_emergency_flex_alert(user_id, context.get("description", ""))
        else:
            return build_emergency_text_alert(user_id, context.get("description", ""))
    else:
        raise ValueError(f"Unknown alert type: {alert_type}")

