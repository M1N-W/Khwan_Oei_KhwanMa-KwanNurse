# -*- coding: utf-8 -*-
"""
Intent handlers for symptom reports, risk assessment, appointments, and triage (KWN-09).
"""
from datetime import datetime
from flask import jsonify
from config import get_logger, LOCAL_TZ, NURSE_GROUP_ID
from utils import (
    parse_date_iso,
    resolve_time_from_params,
    normalize_phone_number,
    is_valid_thai_mobile
)
from services import (
    calculate_symptom_risk,
    calculate_personal_risk,
    create_appointment
)
from services.nlp import analyze_free_text, format_triage_message
from services.notification import send_line_push
from routes.webhook.helpers import _make_dialogflow_response
from services.line_message import quick_reply_item

logger = get_logger(__name__)


def handle_report_symptoms(user_id, params):
    """Handle ReportSymptoms intent"""
    pain = params.get('pain_score')
    wound = params.get('wound_status')
    fever = params.get('fever_check')
    mobility = params.get('mobility_status')
    # Phase 2-A: optional neuro branch (ชา / อ่อนแรง / ปวดร้าว).
    neuro = (
        params.get('neuro_status')
        or params.get('neuro')
        or params.get('numbness')
    )

    # Validate required parameters
    missing = []
    if pain is None or str(pain).strip() == "":
        missing.append("ระดับความปวด (0-10)")
    if not wound:
        missing.append("สภาพแผล")
    if not fever:
        missing.append("อาการไข้")
    if not mobility:
        missing.append("การเคลื่อนไหว")

    if missing:
        ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยค่ะ"
        quick_replies = None
        if pain is None or str(pain).strip() == "":
            quick_replies = [
                quick_reply_item("🟢 0-2 (ปวดน้อย)", "2"),
                quick_reply_item("🟡 3-5 (ปวดปานกลาง)", "5"),
                quick_reply_item("🟠 6-7 (ปวดมาก)", "7"),
                quick_reply_item("🔴 8-10 (ปวดรุนแรง)", "9"),
            ]
        elif not wound:
            quick_replies = [
                quick_reply_item("🟢 แผลแห้งดี", "แผลแห้งดี"),
                quick_reply_item("🟡 แผลซึม/แดง", "แผลแดงซึม"),
                quick_reply_item("🔴 แผลบวม/มีหนอง", "แผลบวมหนอง"),
            ]
        elif not fever:
            quick_replies = [
                quick_reply_item("🟢 ไม่มีไข้", "ไม่มีไข้"),
                quick_reply_item("🔴 มีไข้ตัวร้อน", "มีไข้"),
            ]
        elif not mobility:
            quick_replies = [
                quick_reply_item("🟢 เดินได้ปกติ", "เดินได้ปกติ"),
                quick_reply_item("🟡 ต้องพยุงเดิน", "ต้องพยุง"),
                quick_reply_item("🔴 เดินไม่ได้เลย", "เดินไม่ได้"),
            ]
        return jsonify(_make_dialogflow_response(ask, quick_replies)), 200

    # Calculate risk
    result = calculate_symptom_risk(user_id, pain, wound, fever, mobility, neuro=neuro)

    # T3 (S2-3): Auto-trigger photography guide on wound keywords
    _WOUND_KEYWORDS = ("หนอง", "บวม", "อักเสบ", "แดง", "ซึม", "ฉีก", "เปิด", "แผล",
                       "wound", "pus", "infected", "swollen")
    wound_lower = str(wound or "").lower()
    if user_id and any(kw in wound_lower for kw in _WOUND_KEYWORDS):
        try:
            from services.line_message import build_wound_photography_guide, push_rich_message
            guide_msg = build_wound_photography_guide()
            push_rich_message([guide_msg], user_id)
        except Exception:
            logger.exception("Failed to push proactive photography guide user=%s", user_id)

    # T6 (S2-3): Proactive education push after ReportSymptoms
    from config import ENABLE_RICH_MESSAGES
    if ENABLE_RICH_MESSAGES and user_id:
        try:
            from services.patient_profile import get_or_build_profile
            from services.education import recommend_guides
            from services.line_message import build_education_carousel, push_rich_message
            profile = get_or_build_profile(user_id, params)
            recs = recommend_guides(profile, top_n=3)
            if recs:
                carousel = build_education_carousel(recs)
                push_rich_message([carousel], user_id)
        except Exception:
            logger.exception("Failed to push proactive education user=%s", user_id)

    return jsonify({"fulfillmentText": result}), 200


def handle_assess_risk(user_id, params):
    """Handle AssessRisk intent"""
    age = params.get('age')
    weight = params.get('weight')
    height = params.get('height')
    disease = params.get('disease') or params.get('diseases')
    
    # Validate required parameters
    missing = []
    if age is None or str(age).strip() == "":
        missing.append("อายุ")
    if weight is None or str(weight).strip() == "":
        missing.append("น้ำหนัก (กิโลกรัม)")
    if height is None or str(height).strip() == "":
        missing.append("ส่วนสูง (เซนติเมตร)")
    if not disease:
        missing.append("โรคประจำตัว (หรือพิมพ์ 'ไม่มี')")
    
    if missing:
        ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยค่ะ"
        return jsonify({"fulfillmentText": ask}), 200
    
    # Calculate risk
    result = calculate_personal_risk(user_id, age, weight, height, disease)
    return jsonify({"fulfillmentText": result}), 200


def handle_request_appointment(user_id, params):
    """Handle RequestAppointment intent"""
    preferred_date_raw = (params.get('date') or 
                         params.get('preferred_date') or 
                         params.get('date-original'))
    preferred_time_raw = params.get('time') or params.get('preferred_time')
    timeofday_raw = params.get('timeofday') or params.get('time_of_day')
    reason = params.get('reason') or params.get('symptom') or params.get('description')
    name = params.get('name') or None
    phone_raw = params.get('phone-number') or params.get('phone') or None
    
    # Parse date and time
    preferred_date = parse_date_iso(preferred_date_raw)
    preferred_time = resolve_time_from_params(preferred_time_raw, timeofday_raw)
    
    # Validate required parameters
    missing = []
    
    if not preferred_date:
        missing.append("วันที่นัด (เช่น 25 มกราคม หรือ 2026-01-25)")
    else:
        # Check if date is in the past
        today_local = datetime.now(tz=LOCAL_TZ).date()
        if preferred_date < today_local:
            return jsonify({
                "fulfillmentText": "⚠️ วันที่ที่เลือกเป็นอดีตแล้ว กรุณาเลือกวันที่ในอนาคตค่ะ"
            }), 200
    
    if not preferred_time:
        missing.append("เวลานัด (เช่น 09:00 หรือ 'เช้า'/'บ่าย')")
    
    if not reason:
        missing.append("เหตุผลการนัด (เช่น เปลี่ยนผ้าพันแผล, ตรวจแผล)")
    
    # Validate phone if provided
    phone_norm = normalize_phone_number(phone_raw) if phone_raw else None
    if phone_norm and not is_valid_thai_mobile(phone_norm):
        return jsonify({
            "fulfillmentText": "⚠️ เบอร์โทรศัพท์ไม่ถูกต้อง กรุณาพิมพ์เป็นตัวเลข 10 หลัก (เช่น 0812345678)"
        }), 200
    
    if missing:
        ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยค่ะ"
        return jsonify({"fulfillmentText": ask}), 200
    
    # Create appointment
    pd_str = preferred_date.isoformat()
    pt_str = preferred_time
    
    success, message = create_appointment(
        user_id, name, phone_norm, pd_str, pt_str, reason
    )
    
    return jsonify({"fulfillmentText": message}), 200


def handle_free_text_symptom(user_id, params, query_text):
    """Handle FreeTextSymptom intent (Phase 2-E)."""
    try:
        text = (
            params.get('symptom_text')
            or params.get('description')
            or params.get('text')
            or query_text
            or ''
        )
        if not text or not str(text).strip():
            return jsonify({
                "fulfillmentText": (
                    "เล่าอาการให้ฟังหน่อยค่ะ เช่น\n"
                    "\"แผลบวมแดง ปวด 7/10 มีไข้นิดหน่อย เดินไม่ค่อยไหว\""
                )
            }), 200

        from routes.webhook import analyze_free_text
        result = analyze_free_text(str(text))

        from services.i18n import detect_language
        lang = detect_language(str(text))

        logger.info(
            "FreeTextSymptom triage: level=%s source=%s flags=%s lang=%s",
            result.get('risk_level'), result.get('source'), result.get('flags'), lang,
        )

        reply = format_triage_message(result, lang=lang)

        if result.get('risk_level') == 'high' and NURSE_GROUP_ID:
            try:
                flags = ", ".join(result.get('flags') or []) or "-"
                summary = result.get('summary') or "-"
                alert = (
                    "🚨 รายงานอาการจากแชต (เสี่ยงสูง)\n"
                    f"👤 ผู้ป่วย: {user_id}\n"
                    f"🔎 Flags: {flags}\n"
                    f"📋 สรุป: {summary}\n"
                    "กรุณาติดต่อกลับโดยเร็ว"
                )
                send_line_push(alert, NURSE_GROUP_ID)
            except Exception:
                logger.exception("Failed to send high-risk free-text alert")

        return jsonify({"fulfillmentText": reply}), 200

    except Exception:
        logger.exception("Error in FreeTextSymptom handler")
        return jsonify({
            "fulfillmentText": "ขอโทษค่ะ ระบบประเมินข้อความขัดข้อง กรุณาลองใหม่"
        }), 200
