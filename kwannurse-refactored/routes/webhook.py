# -*- coding: utf-8 -*-
"""
Webhook Routes Module
Handles Dialogflow webhook endpoints
"""
import json
import os
from datetime import datetime
from flask import request, jsonify
from config import get_logger, LOCAL_TZ
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

logger = get_logger(__name__)


def register_routes(app):
    """Register all webhook routes with Flask app"""
    
    @app.route('/', methods=['GET', 'HEAD'])
    def health_check():
        """Health check endpoint for monitoring services"""
        return jsonify({
            "status": "ok",
            "service": "KwanNurse-Bot v3.0",
            "version": "3.0 - Perfect Core (Refactored)",
            "features": ["ReportSymptoms", "AssessRisk", "RequestAppointment"],
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat()
        }), 200
    
    @app.route('/webhook', methods=['POST'])
    def webhook():
        """Main Dialogflow webhook endpoint"""
        req = request.get_json(silent=True, force=True)
        if not req:
            return jsonify({"fulfillmentText": "Request body empty"}), 400
        
        try:
            intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
            params = req.get('queryResult', {}).get('parameters', {}) or {}
            user_id = req.get('session', 'unknown').split('/')[-1]
        except Exception:
            logger.exception("Error parsing request")
            return jsonify({
                "fulfillmentText": "เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้ง"
            }), 200
        
        logger.info("Intent: %s | User: %s | Params: %s", 
                   intent, user_id, json.dumps(params, ensure_ascii=False))
        
        # Route to appropriate handler
        if intent == 'ReportSymptoms':
            return handle_report_symptoms(user_id, params)
        
        elif intent == 'AssessPersonalRisk' or intent == 'AssessRisk':
            return handle_assess_risk(user_id, params)
        
        elif intent == 'RequestAppointment':
            return handle_request_appointment(user_id, params)
        
        elif intent == 'GetGroupID':
            return handle_get_group_id()
        
        else:
            return handle_unknown_intent(intent)


def handle_report_symptoms(user_id, params):
    """Handle ReportSymptoms intent"""
    pain = params.get('pain_score')
    wound = params.get('wound_status')
    fever = params.get('fever_check')
    mobility = params.get('mobility_status')
    
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
        return jsonify({"fulfillmentText": ask}), 200
    
    # Calculate risk
    result = calculate_symptom_risk(user_id, pain, wound, fever, mobility)
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


def handle_get_group_id():
    """Handle GetGroupID debug intent"""
    return jsonify({
        "fulfillmentText": f"🔧 Debug Info:\nNURSE_GROUP_ID: {os.environ.get('NURSE_GROUP_ID', 'Not Set')}"
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
            f"• นัดหมายพยาบาล"
        )
    }), 200
