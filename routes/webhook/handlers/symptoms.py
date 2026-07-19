# -*- coding: utf-8 -*-
"""
Intent handlers for symptom reports, risk assessment, appointments, and triage (KWN-09).
"""
from datetime import datetime
import re
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


# Human-readable labels for each symptom slot (used in focused ask prompts).
SLOT_LABELS = {
    "pain":     "ระดับความปวด (1-5)",
    "wound":    "สภาพแผล",
    "fever":    "อาการไข้",
    "mobility": "การเคลื่อนไหว",
}


def _report_symptoms_context(params=None, lifespan_count=5):
    """Keep symptom slot filling in the runtime-owned Dialogflow context."""
    from flask import has_request_context, request as flask_req

    if not has_request_context():
        return None
    req_json = flask_req.get_json(silent=True, force=True) or {}
    session = req_json.get("session")
    if not session:
        return None
    context = {
        "name": f"{session}/contexts/reportsymptoms_dialog_context",
        "lifespanCount": lifespan_count,
    }
    if params:
        context["parameters"] = dict(params)
    return [context]


def handle_report_symptoms(user_id, params):
    """Handle ReportSymptoms intent"""
    pain = params.get('pain_score')
    
    # Map pain score 1-5 to standard clinical 0-10 VAS score if it's within 1-5 range
    if pain is not None and str(pain).strip() != "":
        try:
            val = int(float(pain))
            if 1 <= val <= 5:
                # Map 1->0, 2->2, 3->5, 4->7, 5->9
                pain = {1: 0, 2: 2, 3: 5, 4: 7, 5: 9}[val]
        except (ValueError, TypeError):
            pass

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
        missing.append("ระดับความปวด (1-5)")
    if not wound:
        missing.append("สภาพแผล")
    if not fever:
        missing.append("อาการไข้")
    if not mobility:
        missing.append("การเคลื่อนไหว")

    if missing:
        # Collect one slot at a time — show quick replies only for the first missing field.
        # This keeps the ask prompt aligned with the buttons presented to the patient.
        if pain is None or str(pain).strip() == "":
            first_missing_key = "pain"
            quick_replies = [
                quick_reply_item("🟢 1 (ปวดน้อย)", "1"),
                quick_reply_item("🟡 2 (ปวดเล็กน้อย)", "2"),
                quick_reply_item("🟠 3 (ปวดปานกลาง)", "3"),
                quick_reply_item("🔴 4 (ปวดมาก)", "4"),
                quick_reply_item("🚨 5 (ปวดรุนแรง)", "5"),
            ]
            ask = (
                "วันนี้ระดับความปวดของคนไข้อยู่ที่ระดับใดคะ? (กรุณาเลือก 1-5):\n\n"
                "🟢 1: ไม่ปวดเลย / ปวดน้อยมาก (มีตึงเล็กน้อย)\n"
                "🟡 2: ปวดเล็กน้อย (ทำงาน/กิจกรรมได้ปกติ)\n"
                "🟠 3: ปวดปานกลาง (เริ่มรบกวนกิจกรรม/ต้องพัก)\n"
                "🔴 4: ปวดมาก (รบกวนมาก/เริ่มนอนไม่หลับ)\n"
                "🚨 5: ปวดรุนแรงที่สุด (ทรมานมาก/ทนไม่ไหว)"
            )
            return jsonify(_make_dialogflow_response(
                ask,
                quick_replies,
                output_contexts=_report_symptoms_context(params),
            )), 200
        elif not wound:
            first_missing_key = "wound"
            quick_replies = [
                quick_reply_item("🟢 แผลแห้งดี", "แผลแห้งดี"),
                quick_reply_item("🟡 แผลซึม/แดง", "แผลแดงซึม"),
                quick_reply_item("🔴 แผลบวม/มีหนอง", "แผลบวมหนอง"),
            ]
            ask = f"กรุณาระบุ {SLOT_LABELS[first_missing_key]} ด้วยค่ะ"
            return jsonify(_make_dialogflow_response(
                ask,
                quick_replies,
                output_contexts=_report_symptoms_context(params),
            )), 200
        elif not fever:
            first_missing_key = "fever"
            quick_replies = [
                quick_reply_item("🟢 ไม่มีไข้", "ไม่มีไข้"),
                quick_reply_item("🔴 มีไข้ตัวร้อน", "มีไข้"),
            ]
            ask = f"กรุณาระบุ {SLOT_LABELS[first_missing_key]} ด้วยค่ะ"
            return jsonify(_make_dialogflow_response(
                ask,
                quick_replies,
                output_contexts=_report_symptoms_context(params),
            )), 200
        else:  # mobility
            first_missing_key = "mobility"
            quick_replies = [
                quick_reply_item("🟢 เดินได้ปกติ", "เดินได้ปกติ"),
                quick_reply_item("🟡 ต้องพยุงเดิน", "ต้องพยุง"),
                quick_reply_item("🔴 เดินไม่ได้เลย", "เดินไม่ได้"),
            ]
            ask = f"กรุณาระบุ {SLOT_LABELS[first_missing_key]} ด้วยค่ะ"
            return jsonify(_make_dialogflow_response(
                ask,
                quick_replies,
                output_contexts=_report_symptoms_context(params),
            )), 200

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

    return jsonify(_make_dialogflow_response(
        result,
        output_contexts=_report_symptoms_context(lifespan_count=0),
    )), 200


def handle_assess_risk(user_id, params):
    """Handle AssessRisk intent"""
    age = params.get('age')
    weight = params.get('weight')
    height = params.get('height')
    disease = params.get('disease') or params.get('diseases')
    
    if age is None or str(age).strip() == "":
        ask = "กรุณาระบุ อายุ ของคนไข้ด้วยค่ะ (เช่น 45)"
        return jsonify(_make_dialogflow_response(ask)), 200
    if weight is None or str(weight).strip() == "":
        ask = "กรุณาระบุ น้ำหนัก (กิโลกรัม) ของคนไข้ด้วยค่ะ (เช่น 65)"
        return jsonify(_make_dialogflow_response(ask)), 200
    if height is None or str(height).strip() == "":
        ask = "กรุณาระบุ ส่วนสูง (เซนติเมตร) ของคนไข้ด้วยค่ะ (เช่น 170)"
        return jsonify(_make_dialogflow_response(ask)), 200
    if not disease:
        quick_replies = [
            quick_reply_item("🟢 ไม่มีโรคประจำตัว", "ไม่มี"),
        ]
        ask = "กรุณาระบุ โรคประจำตัว (หรือพิมพ์/เลือก 'ไม่มี') ด้วยค่ะ"
        return jsonify(_make_dialogflow_response(ask, quick_replies)), 200
    
    # Calculate risk
    result = calculate_personal_risk(user_id, age, weight, height, disease)
    return jsonify({"fulfillmentText": result}), 200


def handle_request_appointment(user_id, params):
    """Handle RequestAppointment intent sequentially"""
    from flask import has_request_context, request as flask_req
    session = None
    ctx_params = {}
    query_text = ""
    
    if has_request_context():
        req_json = flask_req.get_json(silent=True, force=True) or {}
        session = req_json.get("session")
        query_text = req_json.get('queryResult', {}).get('queryText', '')
        
        # Extract previous parameters from requestappointment_dialog_context
        contexts = req_json.get('queryResult', {}).get('outputContexts', [])
        for ctx in contexts:
            name_str = ctx.get('name', '')
            if "requestappointment_dialog_context" in name_str:
                ctx_params = ctx.get('parameters', {}) or {}
                break

    # --- Smart merge (Bug #3 fix) ---
    # Context params represent what the user has ALREADY provided slot-by-slot.
    # Fresh Dialogflow params may include a full @sys.date even when the user only
    # answered one part (e.g. typed "กันยายน" → Dialogflow infers date="2026-09-01").
    # Strategy:
    #   1. Start with context as authoritative base.
    #   2. Fill in missing slots from fresh params — but NEVER overwrite a slot
    #      that context already holds.
    merged_params = dict(ctx_params)
    for k, v in (params or {}).items():
        if k not in merged_params or not merged_params.get(k):
            merged_params[k] = v

    # Backward compatibility: if user gave a full date upfront (no slots collected
    # yet), expand @sys.date into apt_day / apt_month / apt_year.
    # If ANY slot is already in context, skip this block — we are mid-collection
    # and must not let @sys.date overwrite what was already confirmed.
    _slots_already_started = any([
        ctx_params.get("apt_day"),
        ctx_params.get("apt_month"),
        ctx_params.get("apt_year"),
    ])
    preferred_date_raw = (merged_params.get('date') or
                          merged_params.get('preferred_date') or
                          merged_params.get('date-original'))
    preferred_time_raw = merged_params.get('time') or merged_params.get('preferred_time')
    timeofday_raw = merged_params.get('timeofday') or merged_params.get('time_of_day')
    reason_raw = merged_params.get('reason') or merged_params.get('symptom') or merged_params.get('description')

    if preferred_date_raw and not _slots_already_started:
        # Fresh turn: user provided a full date string → decompose it.
        from utils.parsers import parse_date_iso
        pd = parse_date_iso(preferred_date_raw)
        if pd:
            merged_params["apt_day"]   = str(pd.day)
            merged_params["apt_month"] = str(pd.month)
            merged_params["apt_year"]  = str(pd.year)

    from utils.parsers import parse_thai_colloquial_time, resolve_time_from_params

    def looks_like_time(value):
        if value in (None, ""):
            return False
        text = str(value).strip()
        return bool(parse_thai_colloquial_time(text)) or bool(
            re.fullmatch(r"\d{1,2}\s*[:.]\s*\d{2}", text)
        )
    pt = resolve_time_from_params(preferred_time_raw, timeofday_raw)
    if pt:
        merged_params["preferred_time"] = pt

    if reason_raw:
        if isinstance(reason_raw, dict):
            for k in ("symptom", "value", "name", "original"):
                if k in reason_raw and isinstance(reason_raw[k], str):
                    reason_raw = reason_raw[k]
                    break
        if isinstance(reason_raw, str) and not looks_like_time(reason_raw):
            merged_params["reason"] = reason_raw
        elif looks_like_time(reason_raw):
            # Dialogflow can copy the latest time entity into the reason slot.
            # Remove that inferred value before the state machine checks reason.
            merged_params.pop("reason", None)

    # 1. Day Collection
    if not merged_params.get("apt_day"):
        cleaned_num = "".join(ch for ch in query_text if ch.isdigit())
        if cleaned_num:
            val = int(cleaned_num)
            if 1 <= val <= 31:
                merged_params["apt_day"] = str(val)
                
    def appointment_context():
        if not session:
            return None
        return [{
            "name": f"{session}/contexts/requestappointment_dialog_context",
            "lifespanCount": 5,
            "parameters": dict(merged_params),
        }]

    output_contexts = appointment_context()

    if not merged_params.get("apt_day"):
        ask = "กรุณาพิมพ์วันที่ที่ต้องการนัดหมาย (ตัวเลข 1-31) ค่ะ (เช่น 28)"
        return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200

    # 2. Month Collection
    TH_MONTHS = {
        "มกราคม": 1, "ม.ค.": 1, "มกรา": 1,
        "กุมภาพันธ์": 2, "ก.พ.": 2, "กุมภา": 2,
        "มีนาคม": 3, "มี.ค.": 3, "มีนา": 3,
        "เมษายน": 4, "เม.ย.": 4, "เมษา": 4,
        "พฤษภาคม": 5, "พ.ค.": 5, "พฤษภา": 5,
        "มิถุนายน": 6, "มิ.ย.": 6, "มิถุนา": 6,
        "กรกฎาคม": 7, "ก.ค.": 7, "กรกฎา": 7,
        "สิงหาคม": 8, "ส.ค.": 8, "สิงหา": 8,
        "กันยายน": 9, "ก.ย.": 9, "กันยา": 9,
        "ตุลาคม": 10, "ต.ค.": 10, "ตุลา": 10,
        "พฤศจิกายน": 11, "พ.ย.": 11, "พฤศจิกา": 11,
        "ธันวาคม": 12, "ธ.ค.": 12, "ธันวา": 12
    }
    
    if not merged_params.get("apt_month"):
        norm_text = query_text.strip().replace(" ", "")
        month_val = None
        for k, v in TH_MONTHS.items():
            if k in norm_text:
                month_val = v
                break
        if month_val:
            merged_params["apt_month"] = str(month_val)
        output_contexts = appointment_context()

    if not merged_params.get("apt_month"):
        ask = "กรุณาระบุเดือนที่ต้องการนัดหมายค่ะ (เช่น พฤศจิกายน หรือ พ.ย.)"
        return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200

    # 3. Year Collection
    if not merged_params.get("apt_year"):
        cleaned_year = "".join(ch for ch in query_text if ch.isdigit())
        if len(cleaned_year) >= 2:
            val = int(cleaned_year)
            year_ce = None
            if val > 2400:
                year_ce = val - 543
            elif 2000 <= val < 2100:
                year_ce = val
            elif 60 <= val <= 99:
                year_ce = (2500 + val) - 543
            elif 20 <= val <= 59:
                year_ce = 2000 + val
                
            if year_ce:
                merged_params["apt_year"] = str(year_ce)
        output_contexts = appointment_context()

    if not merged_params.get("apt_year"):
        ask = "กรุณาระบุ ปี พ.ศ. ของการนัดหมายค่ะ (เช่น 2569)"
        return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200

    # Validateconstructed date correctness
    try:
        import datetime as dt_module
        preferred_date = dt_module.date(
            int(merged_params["apt_year"]),
            int(merged_params["apt_month"]),
            int(merged_params["apt_day"])
        )
    except (ValueError, TypeError):
        # Invalid date like Feb 30, clear parameters and prompt again
        for k in ("apt_day", "apt_month", "apt_year"):
            merged_params.pop(k, None)
        ask = "⚠️ วันที่ระบุไม่ถูกต้องตามปฏิทิน กรุณาระบุ วันที่ นัดหมายใหม่อีกครั้งค่ะ (ตัวเลข 1-31)"
        return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200

    # 4. Time Collection
    from utils.parsers import parse_thai_colloquial_time, resolve_time_from_params
    
    time_collected_this_turn = False
    if not merged_params.get("preferred_time"):
        if query_text == "ระบุเวลาเอง":
            merged_params["waiting_for_custom_time"] = "true"
            ask = "กรุณาพิมพ์เวลาที่ต้องการนัดหมายได้เลยค่ะ (เช่น 14:30 หรือ บ่ายสองโมงครึ่ง)"
            output_contexts = appointment_context()
            return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200
            
        if merged_params.get("waiting_for_custom_time") == "true":
            parsed_t = parse_thai_colloquial_time(query_text)
            if parsed_t:
                merged_params["preferred_time"] = parsed_t
                merged_params.pop("waiting_for_custom_time", None)
                time_collected_this_turn = True
                output_contexts = appointment_context()
            else:
                ask = "⚠️ ไม่สามารถเข้าใจเวลาที่ระบุได้ กรุณาพิมพ์เวลาใหม่อีกครั้งค่ะ (เช่น 14:30 หรือ บ่ายสองโมงครึ่ง)"
                return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200
        else:
            # Check if user typed or sent time parameter directly
            parsed_t = parse_thai_colloquial_time(query_text)
            if not parsed_t:
                parsed_t = resolve_time_from_params(params.get('time'), params.get('timeofday'))
            if parsed_t:
                merged_params["preferred_time"] = parsed_t
                time_collected_this_turn = True
                output_contexts = appointment_context()

    if not merged_params.get("preferred_time"):
        quick_replies = [
            quick_reply_item("🟢 ช่วงเช้า (09:00 - 12:00)", "เช้า"),
            quick_reply_item("🔵 ช่วงบ่าย (13:00 - 16:00)", "บ่าย"),
            quick_reply_item("🕒 ระบุเวลาเอง", "ระบุเวลาเอง"),
        ]
        ask = "กรุณาระบุ เวลาที่ต้องการนัดหมาย ด้วยค่ะ"
        return jsonify(_make_dialogflow_response(ask, quick_replies, output_contexts=output_contexts)), 200

    # 5. Reason Collection
    if not merged_params.get("reason"):
        from services.patient_profile import is_registration_trigger_text
        if (
            query_text
            and not is_registration_trigger_text(query_text)
            and query_text != "ระบุเวลาเอง"
            and not time_collected_this_turn
            and not looks_like_time(query_text)
        ):
            merged_params["reason"] = query_text
        output_contexts = appointment_context()

    if not merged_params.get("reason"):
        quick_replies = [
            quick_reply_item("🟢 ตรวจแผลหลังผ่าตัด", "ตรวจแผลหลังผ่าตัด"),
            quick_reply_item("🟡 เปลี่ยนผ้าพันแผล", "เปลี่ยนผ้าพันแผล"),
            quick_reply_item("🔵 ปรึกษาอาการทั่วไป", "ปรึกษาอาการทั่วไป"),
        ]
        ask = "กรุณาระบุ เหตุผลการนัดหมาย ด้วยค่ะ"
        return jsonify(_make_dialogflow_response(ask, quick_replies, output_contexts=output_contexts)), 200

    # Date Validation (Future check)
    today_local = datetime.now(tz=LOCAL_TZ).date()
    if preferred_date < today_local:
        for k in ("apt_day", "apt_month", "apt_year"):
            merged_params.pop(k, None)
        ask = "⚠️ วันที่ที่เลือกเป็นอดีตแล้ว กรุณาเลือกวันที่ในอนาคตค่ะ (เริ่มที่การระบุวันที่ 1-31)"
        return jsonify(_make_dialogflow_response(ask, output_contexts=output_contexts)), 200

    # Final Execution
    from services.patient_profile import get_or_build_profile, _coerce_string
    profile = get_or_build_profile(user_id)
    
    name_param = merged_params.get('name') or (params and params.get('name'))
    if isinstance(name_param, dict) and "name" in name_param:
        name_param = name_param["name"]
    name = _coerce_string(name_param)
    if not name:
        name = ((profile.get('first_name') or "") + " " + (profile.get('last_name') or "")).strip()
    if not name:
        name = "คนไข้"
        
    phone_param = merged_params.get('phone-number') or merged_params.get('phone') or (params and (params.get('phone-number') or params.get('phone')))
    phone_norm = normalize_phone_number(phone_param) if phone_param else None
    if not phone_norm:
        phone_norm = profile.get('phone') or ""
        
    preferred_time = merged_params["preferred_time"]
    reason = merged_params["reason"]
    
    success, message = create_appointment(
        user_id, name, phone_norm, preferred_date.isoformat(), preferred_time, reason
    )
    
    # Clear appointment context upon completion
    clear_contexts = None
    if session:
        clear_contexts = [{
            "name": f"{session}/contexts/requestappointment_dialog_context",
            "lifespanCount": 0
        }]
        
    return jsonify(_make_dialogflow_response(message, output_contexts=clear_contexts)), 200


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
                from services.notification import _get_patient_prefix_label
                patient_label = _get_patient_prefix_label(user_id)
                flags = ", ".join(result.get('flags') or []) or "-"
                summary = result.get('summary') or "-"
                alert = (
                    "🚨 รายงานอาการจากแชต (เสี่ยงสูง)\n"
                    f"👤 ผู้ป่วย: {patient_label}\n"
                    f"🆔 User ID: {user_id}\n"
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
