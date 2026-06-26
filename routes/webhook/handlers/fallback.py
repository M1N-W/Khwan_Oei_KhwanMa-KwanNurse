# -*- coding: utf-8 -*-
"""
Intent handlers for fallback, unknown intents, teleconsult flow, and knowledge search (KWN-09).
"""
import os
from datetime import datetime
from flask import jsonify
from config import get_logger, LOCAL_TZ, OFFICE_HOURS, DEBUG
from database.education_logs import save_education_view
from services import (
    get_knowledge_menu,
    get_wound_care_guide,
    get_physical_therapy_guide,
    get_dvt_prevention_guide,
    get_medication_guide,
    get_warning_signs_guide
)
from services.teleconsult import (
    is_office_hours,
    get_category_menu,
    parse_category_choice,
    start_teleconsult,
    cancel_consultation,
    handle_after_hours_choice as teleconsult_after_hours_choice
)

logger = get_logger(__name__)

# Reverse map: display_name -> canonical key (used for EducationLog audit).
_TOPIC_DISPLAY_TO_KEY = {
    'การดูแลแผล': 'wound_care',
    'กายภาพบำบัด': 'physical_therapy',
    'ป้องกันลิ่มเลือด': 'dvt_prevention',
    'การรับประทานยา': 'medication',
    'สัญญาณอันตราย': 'warning_signs',
}

# Map of topic keywords (Thai + English) → (display name, guide function).
_KNOWLEDGE_TOPIC_MAP = {
    'wound_care': ('การดูแลแผล', get_wound_care_guide),
    'ดูแลแผล': ('การดูแลแผล', get_wound_care_guide),
    'การดูแลแผล': ('การดูแลแผล', get_wound_care_guide),
    'แผล': ('การดูแลแผล', get_wound_care_guide),

    'physical_therapy': ('กายภาพบำบัด', get_physical_therapy_guide),
    'กายภาพบำบัด': ('กายภาพบำบัด', get_physical_therapy_guide),
    'กายภาพ': ('กายภาพบำบัด', get_physical_therapy_guide),
    'ออกกำลังกาย': ('กายภาพบำบัด', get_physical_therapy_guide),

    'dvt': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),
    'dvt_prevention': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),
    'ลิ่มเลือด': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),
    'ป้องกันลิ่มเลือด': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),

    'medication': ('การรับประทานยา', get_medication_guide),
    'ยา': ('การรับประทานยา', get_medication_guide),
    'ทานยา': ('การรับประทานยา', get_medication_guide),
    'รับประทานยา': ('การรับประทานยา', get_medication_guide),
    'วิธีทานยา': ('การรับประทานยา', get_medication_guide),

    'warning_signs': ('สัญญาณอันตราย', get_warning_signs_guide),
    'สัญญาณอันตราย': ('สัญญาณอันตราย', get_warning_signs_guide),
    'อาการอันตราย': ('สัญญาณอันตราย', get_warning_signs_guide),
    'อันตราย': ('สัญญาณอันตราย', get_warning_signs_guide),
    'เมื่อไหร่ต้องพบหมอ': ('สัญญาณอันตราย', get_warning_signs_guide),
}

# Words that mean "show me the menu" — bypass topic resolution.
_KNOWLEDGE_MENU_TRIGGERS = {'menu', 'เมนู', 'ความรู้', 'knowledge'}


def _resolve_knowledge_topic(text):
    """Find the best-matching knowledge topic for raw user text."""
    if not text:
        return None
    norm = str(text).lower().strip()
    if not norm:
        return None
    # 1. Exact match
    if norm in _KNOWLEDGE_TOPIC_MAP:
        return _KNOWLEDGE_TOPIC_MAP[norm]
    # 2. Substring — prefer longest key so multi-word phrases beat short ones
    matches = [
        (key, val) for key, val in _KNOWLEDGE_TOPIC_MAP.items()
        if key in norm
    ]
    if not matches:
        return None
    matches.sort(key=lambda kv: -len(kv[0]))
    return matches[0][1]


def handle_get_knowledge(user_id, params, query_text=""):
    """Handle GetKnowledge intent."""
    topic_param = params.get('topic') or params.get('knowledge_topic')
    topic_str = str(topic_param).strip() if topic_param else ""

    if (not topic_str or topic_str.lower() in _KNOWLEDGE_MENU_TRIGGERS) and \
       (not query_text or query_text.lower().strip() in _KNOWLEDGE_MENU_TRIGGERS):
        result = get_knowledge_menu()
        return jsonify({"fulfillmentText": result}), 200

    # 1. Try Dialogflow-extracted topic first
    resolved = _resolve_knowledge_topic(topic_str) if topic_str else None
    # 2. Fallback to raw user text
    if resolved is None and query_text:
        resolved = _resolve_knowledge_topic(query_text)

    if resolved:
        topic_name, guide_func = resolved
        
        # Resolve guide_func dynamically from routes.webhook to support test mocks/patches
        try:
            from routes.webhook import (
                get_wound_care_guide,
                get_physical_therapy_guide,
                get_dvt_prevention_guide,
                get_medication_guide,
                get_warning_signs_guide
            )
            func_name = guide_func.__name__
            webhook_guides = {
                'get_wound_care_guide': get_wound_care_guide,
                'get_physical_therapy_guide': get_physical_therapy_guide,
                'get_dvt_prevention_guide': get_dvt_prevention_guide,
                'get_medication_guide': get_medication_guide,
                'get_warning_signs_guide': get_warning_signs_guide
            }
            if func_name in webhook_guides:
                guide_func = webhook_guides[func_name]
        except Exception:
            pass

        logger.info(
            "Knowledge request: %s (param=%r query=%r)",
            topic_name, topic_str, query_text,
        )
        try:
            canonical = _TOPIC_DISPLAY_TO_KEY.get(topic_name, topic_name)
            from routes.webhook import save_education_view
            save_education_view(
                user_id=user_id,
                topic=canonical,
                source="GetKnowledge",
                personalized=False,
            )
        except Exception:
            logger.exception("EducationLog write failed (non-fatal)")
        return jsonify({"fulfillmentText": guide_func()}), 200

    shown = topic_str or query_text or ""
    return jsonify({
        "fulfillmentText": (
            f"ขอโทษค่ะ ไม่พบหัวข้อ '{shown}'\n\n"
            f"กรุณาพิมพ์ 'ความรู้' เพื่อดูหัวข้อที่มีค่ะ"
        )
    }), 200


def handle_get_group_id():
    """Handle GetGroupID debug intent."""
    if not DEBUG:
        logger.warning("GetGroupID invoked with DEBUG=false; refusing to expose group id")
        return jsonify({
            "fulfillmentText": "ฟีเจอร์นี้ปิดอยู่ในโหมดใช้งานจริง"
        }), 200

    group_id = os.environ.get('NURSE_GROUP_ID', 'Not Set')
    shown = group_id if len(group_id) <= 10 else f"{group_id[:4]}***{group_id[-4:]}"
    return jsonify({
        "fulfillmentText": f"🔧 Debug Info:\nNURSE_GROUP_ID: {shown}"
    }), 200


def handle_contact_nurse(user_id, params, query_text):
    """Handle ContactNurse intent"""
    try:
        logger.info(f"ContactNurse request from {user_id}")
        
        category_param = params.get('issue_category') or params.get('category')
        description_param = params.get('description') or params.get('issue_description')
        
        if category_param:
            issue_type = parse_category_choice(str(category_param))
        else:
            issue_type = parse_category_choice(query_text)
        
        if issue_type:
            description = str(description_param) if description_param else ""
            result = start_teleconsult(user_id, issue_type, description)
            return jsonify({"fulfillmentText": result['message']}), 200
        else:
            menu = get_category_menu()
            
            if not is_office_hours():
                now = datetime.now(tz=LOCAL_TZ)
                current_time = now.strftime("%H:%M")
                menu = (
                    f"⏰ ขณะนี้นอกเวลาทำการ ({current_time} น.)\n"
                    f"เวลาทำการ: {OFFICE_HOURS['start']}-{OFFICE_HOURS['end']} น.\n\n"
                    f"{menu}\n\n"
                    f"💡 หากเป็นเรื่องฉุกเฉิน เลือกหมายเลข 1"
                )
            
            return jsonify({"fulfillmentText": menu}), 200
        
    except Exception as e:
        logger.exception(f"Error in ContactNurse: {e}")
        return jsonify({
            "fulfillmentText": "เกิดข้อผิดพลาด กรุณาลองใหม่ภายหลัง"
        }), 200


def handle_cancel_consultation(user_id):
    """Handle cancellation of consultation"""
    try:
        result = cancel_consultation(user_id)
        return jsonify({"fulfillmentText": result['message']}), 200
    except Exception as e:
        logger.exception(f"Error cancelling consultation: {e}")
        return jsonify({
            "fulfillmentText": "เกิดข้อผิดพลาดในการยกเลิก กรุณาลองใหม่"
        }), 200


def handle_unknown_intent(intent):
    """Handle unknown/unhandled intents"""
    logger.warning("Unhandled intent: %s", intent)
    return jsonify({
        "fulfillmentText": (
            f"ขอโทษค่ะ บอทยังไม่รองรับคำสั่ง '{intent}' ในขณะนี้\n\n"
            f"คุณสามารถใช้ฟีเจอร์หลักได้:\n"
            f"• รายงานอาการ\n"
            f"• ประเมินความเสี่ยง\n"
            f"• นัดหมายพยาบาล\n"
            f"• ความรู้และคำแนะนำ (พิมพ์ 'แนะนำความรู้' สำหรับเฉพาะราย)\n"
            f"• ติดตามหลังจำหน่าย\n"
            f"• ปรึกษาพยาบาล\n"
            f"• เล่าอาการเป็นข้อความอิสระ"
        )
    }), 200


def handle_after_hours_choice(user_id, query_text):
    """Handle AfterHoursChoice intent"""
    result = teleconsult_after_hours_choice(user_id, query_text)
    return jsonify({"fulfillmentText": result['message']}), 200

