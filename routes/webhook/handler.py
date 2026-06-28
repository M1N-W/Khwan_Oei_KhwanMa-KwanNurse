# -*- coding: utf-8 -*-
"""
Webhook routing and entry point (KWN-09).
Maps incoming HTTP webhook requests and dispatches Dialogflow intents to handlers.
"""
import os
from datetime import datetime
from flask import request, jsonify, Response
from config import get_logger, LOCAL_TZ, DEBUG
from utils.pii import scrub_user_id
from services.metrics import incr
from services.security import require_line_signature, require_dialogflow_token
from config import NURSE_GROUP_ID

logger = get_logger(__name__)


def _extract_line_user_id(req: dict) -> str | None:
    """
    Extract the actual LINE User ID from Dialogflow's originalDetectIntentRequest
    when available (e.g. when called via real LINE integration).
    """
    if not isinstance(req, dict):
        return None
        
    original_req = req.get("originalDetectIntentRequest")
    if not isinstance(original_req, dict) or original_req.get("source") != "line":
        return None
    
    payload = original_req.get("payload")
    if not isinstance(payload, dict):
        return None
        
    # Path 1: payload.data.source.userId
    data = payload.get("data")
    if isinstance(data, dict):
        source = data.get("source")
        if isinstance(source, dict):
            user_id = source.get("userId")
            if isinstance(user_id, str):
                return user_id
                
    # Path 2: payload.source.userId
    source = payload.get("source")
    if isinstance(source, dict):
        user_id = source.get("userId")
        if isinstance(user_id, str):
            return user_id
            
    # Path 3: payload.userId
    user_id = payload.get("userId")
    if isinstance(user_id, str):
        return user_id
        
    return None


def _get_clear_all_contexts(session: str | None) -> list[dict]:
    """Return a list of output contexts to clear all active state/slot-filling dialog contexts."""
    if not session:
        return []
    contexts_to_clear = [
        "registering",
        "reportsymptoms_dialog_context",
        "assessrisk_dialog_context",
        "assesspersonalrisk_dialog_context",
        "requestappointment_dialog_context",
    ]
    return [{"name": f"{session}/contexts/{name}", "lifespanCount": 0} for name in contexts_to_clear]


def register_routes(app):
    """Register all webhook routes with Flask app"""
    
    @app.route('/', methods=['GET', 'HEAD'])
    def health_check():
        """Health check endpoint for monitoring services with full configuration status (v5.0)"""
        from config import validate_runtime_config
        config_status = validate_runtime_config()
        
        return jsonify({
            "status": "ok" if config_status["ok"] else "warning",
            "service": "ขวัญเอ๋ยขวัญมา-บอท v5.0",
            "version": "5.0 - Complete (UX/UI Polish)",
            "features": [
                "ReportSymptoms", 
                "AssessRisk", 
                "RequestAppointment", 
                "GetKnowledge",
                "FollowUpReminders",
                "Teleconsult"
            ],
            "diagnostics": {
                "config_ok": config_status["ok"],
                "missing_items": config_status["missing"],
                "can_notify_line": config_status["can_notify"],
                "can_persist_sheets": config_status["can_persist"]
            },
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat()
        }), 200

    @app.route('/healthz', methods=['GET', 'HEAD'])
    def healthz():
        """
        Liveness probeสำหรับ UptimeRobot / Render health check.
        """
        resp = Response("healthy\n", mimetype="text/plain")
        resp.headers["Cache-Control"] = "no-store, no-transform"
        resp.headers["Content-Encoding"] = "identity"
        resp.headers["Vary"] = "Accept-Encoding"
        return resp

    @app.route('/readyz', methods=['GET', 'HEAD'])
    def readyz():
        """
        Readiness probe (P4-3): can reach dependencies (Google Sheets).
        """
        from database.sheets import get_spreadsheet
        checks: dict = {}
        all_ok = True

        runtime_cfg = app.config.get('RUNTIME_CONFIG') or {}
        if runtime_cfg.get('can_persist'):
            try:
                ss = get_spreadsheet()
                checks["sheets"] = "ok" if ss is not None else "unavailable"
                if ss is None:
                    all_ok = False
            except Exception as exc:
                checks["sheets"] = f"error: {type(exc).__name__}"
                all_ok = False
        else:
            checks["sheets"] = "skipped (no credentials configured)"

        status_code = 200 if all_ok else 503
        return jsonify({
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat(),
        }), status_code

    @app.route('/metrics', methods=['GET'])
    def metrics_snapshot():
        """
        Lightweight in-process metrics snapshot.
        """
        from services.metrics import snapshot
        return jsonify({
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat(),
            "counters": snapshot(),
        }), 200

    @app.route('/track/<token>', methods=['GET'])
    def track_survey_click(token):
        """Track survey click (KWN-07) and redirect to Google Form."""
        from database.surveys import mark_survey_clicked
        from flask import redirect
        from services.survey import SURVEY_FORM_URL
        
        survey_url = mark_survey_clicked(token)
        if not survey_url:
            logger.warning("Invalid survey tracking token: %s", token)
            survey_url = SURVEY_FORM_URL
            
        return redirect(survey_url)

    @app.route('/webhook', methods=['POST'])
    @require_dialogflow_token
    def webhook():
        """Main Dialogflow webhook endpoint."""
        req = request.get_json(silent=True, force=True)
        if not req:
            return jsonify({"fulfillmentText": "Request body empty"}), 400
        
        try:
            intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
            if intent in ("PatientIdentity_Fallback", "PatientIdentity_Input"):
                intent = "PatientIdentity"
            params = req.get('queryResult', {}).get('parameters', {}) or {}
            
            # Extract LINE User ID if available, otherwise fallback to Dialogflow session ID
            line_user_id = _extract_line_user_id(req)
            if line_user_id:
                user_id = line_user_id
            else:
                user_id = req.get('session', 'unknown').split('/')[-1]
                
            query_text = req.get('queryResult', {}).get('queryText', '')
            
            # Deterministic router & State Machine: bypass Dialogflow ML misclassification
            if isinstance(query_text, str):
                cleaned_query = query_text.strip().lower()
                
                # Check registration status from the DB to drive the slot-filling state machine
                try:
                    from database.patient_profile import read_patient_profile_result
                    from services.patient_profile import registration_missing_fields
                    read_result = read_patient_profile_result(user_id)
                except Exception:
                    read_result = None

                # Reset/Cancel registration flow if user says cancel while registration is incomplete
                if cleaned_query in ("ยกเลิก", "ยกเลิกคำขอ", "ยกเลิกปรึกษา", "ยกเลิกการลงทะเบียน", "ยกเลิกนัด", "ยกเลิกนัดหมาย"):
                    session = req.get("session")
                    clear_contexts = _get_clear_all_contexts(session)
                    registration_cancelled = False
                    if read_result and read_result.available and read_result.profile:
                        from services.patient_profile import is_registration_complete
                        if not is_registration_complete(read_result.profile):
                            try:
                                from database.patient_profile import upsert_patient_profile
                                from services.patient_profile import invalidate_profile_cache
                                upsert_patient_profile(user_id, {
                                    "first_name": "", "last_name": "", "hn": "", "phone": "", 
                                    "consent_granted": False, "consent_version": "", "consent_at": ""
                                })
                                invalidate_profile_cache(user_id)
                                registration_cancelled = True
                            except Exception:
                                pass
                    if registration_cancelled:
                        msg = "❌ ยกเลิกการลงทะเบียนเรียบร้อยแล้วค่ะ หากต้องการลงทะเบียนใหม่ กรุณาพิมพ์คำว่า 'ลงทะเบียน' อีกครั้งค่ะ"
                    else:
                        msg = "❌ ยกเลิกการทำรายการเรียบร้อยแล้วค่ะ มีอะไรให้ฉันช่วยเหลือเพิ่มเติมไหมคะ?"
                    from routes.webhook.helpers import _make_dialogflow_response
                    return jsonify(_make_dialogflow_response(msg, output_contexts=clear_contexts)), 200

                # AI Mode intercept (only for registered patients)
                if read_result and read_result.available and read_result.profile:
                    profile = read_result.profile
                    from services.patient_profile import registration_missing_fields
                    missing = registration_missing_fields(profile)
                    if not missing:
                        ai_response = handle_ai_mode_intercept(user_id, profile, query_text, intent=intent)
                        if ai_response is not None:
                            return ai_response

                # Core keyword routing
                if cleaned_query in ("ลงทะเบียน", "register", "สมัครสมาชิก", "เข้าสู่ระบบ", "สมัคร"):
                    intent = "PatientIdentity"
                elif cleaned_query in ("ความรู้", "เมนูความรู้", "เมนูความรู้หลัก", "คู่มือ"):
                    intent = "GetKnowledge"
                    params = {}
                elif cleaned_query in ("ปรึกษาพยาบาล", "ติดต่อพยาบาล", "คุยกับพยาบาล"):
                    intent = "ContactNurse"
                    params = {}
                elif cleaned_query in ("ยกเลิก", "ยกเลิกคำขอ", "ยกเลิกปรึกษา"):
                    intent = "CancelConsultation"
                elif cleaned_query in ("แจ้งเรื่องฉุกเฉิน", "รอเวลาทำการ"):
                    intent = "AfterHoursChoice"
                elif read_result and read_result.available and read_result.profile:
                    profile = read_result.profile
                    missing = registration_missing_fields(profile)
                    from routes.webhook.helpers import _REGISTRATION_GATED_INTENTS, _appointment_during_registration_should_reroute
                    should_override = False
                    if missing:
                        if intent not in _REGISTRATION_GATED_INTENTS:
                            should_override = True
                        elif intent == "RequestAppointment" and _appointment_during_registration_should_reroute(user_id, params, query_text):
                            should_override = True

                    if should_override:
                        intent = "PatientIdentity"
                        first_missing = missing[0]
                        if first_missing == "first_name":
                            params = {"first_name": query_text}
                        elif first_missing == "last_name":
                            params = {"last_name": query_text}
                        elif first_missing == "hn":
                            params = {"hn": query_text}
                        elif first_missing == "phone":
                            params = {"phone": query_text}
                        elif first_missing == "consent":
                            params = {"consent": query_text}
        except Exception:
            logger.exception("Error parsing request")
            return jsonify({
                "fulfillmentText": "เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้ง"
            }), 200
        
        masked_user = (user_id[:4] + "***" + user_id[-4:]) if len(user_id) > 10 else "***"
        logger.info("Intent: %s | User: %s | ParamKeys: %s",
                    intent, masked_user, sorted(params.keys()))

        intent_for_metric = (intent or "unknown").replace(".", "_")[:64]
        incr(f"webhook.intent.{intent_for_metric}")

        try:
            return _dispatch_intent(intent, user_id, params, query_text)
        except Exception:
            incr(f"webhook.error.{intent_for_metric}")
            logger.exception(
                "Unhandled exception in intent dispatch (intent=%s user=%s)",
                intent, masked_user,
            )
            return jsonify({
                "fulfillmentText": "ขอโทษค่ะ ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"
            }), 200

    @app.route('/line/webhook', methods=['POST'])
    @require_line_signature
    def line_webhook():
        """Direct LINE Messaging API webhook."""
        body = request.get_json(silent=True) or {}
        events = body.get("events") or []
        for event in events:
            try:
                event_type = event.get("type")
                if event_type == "follow":
                    reply_token = event.get("replyToken")
                    if reply_token:
                        from services.line_message import build_user_manual_flex, build_text_message, reply_rich_message
                        welcome_text = (
                            "สวัสดีค่ะ ยินดีต้อนรับสู่ \"ขวัญเอ๋ยขวัญมา\" บอทดูแลผู้ป่วยหลังผ่าตัดค่ะ\n\n"
                            "เพื่อความปลอดภัยในการดูแลสุขภาพ กรุณาลงทะเบียนข้อมูลผู้ป่วยก่อนเริ่มต้นใช้งานระบบนะคะ"
                        )
                        msg_text = build_text_message(welcome_text)
                        msg_flex = build_user_manual_flex()
                        reply_rich_message(reply_token, [msg_text, msg_flex])
                    continue
                if event_type != "message":
                    continue
                msg = event.get("message") or {}
                msg_type = msg.get("type")
                if msg_type == "image":
                    from routes.webhook import handle_line_image_event
                    handle_line_image_event(event)
                elif msg_type == "audio":
                    from services.voice import handle_voice_event
                    handle_voice_event(event)
            except Exception:
                logger.exception("Error processing LINE event: %s", event.get("type"))
        return jsonify({"status": "ok", "events_received": len(events)}), 200


def _dispatch_intent(intent, user_id, params, query_text):
    """Map a Dialogflow intent name to its handler."""
    from routes.webhook import (
        _registration_gate_response,
        _touch_activity,
        handle_report_symptoms,
        handle_assess_risk,
        handle_request_appointment,
        handle_get_knowledge,
        handle_get_followup_summary,
        handle_contact_nurse,
        handle_cancel_consultation,
        handle_get_group_id,
        handle_free_text_symptom,
        handle_recommend_knowledge,
        handle_patient_identity,
        handle_unknown_intent,
        handle_after_hours_choice
    )

    gated = _registration_gate_response(intent, user_id, query_text)
    if gated is not None:
        return gated

    if intent == 'ReportSymptoms':
        response = handle_report_symptoms(user_id, params)
    elif intent == 'AssessPersonalRisk' or intent == 'AssessRisk':
        response = handle_assess_risk(user_id, params)
    elif intent == 'RequestAppointment':
        from routes.webhook.helpers import _appointment_during_registration_should_reroute
        if _appointment_during_registration_should_reroute(user_id, params, query_text):
            logger.info(
                "Rerouting RequestAppointment -> PatientIdentity (query=%r user=%s)",
                query_text,
                _mask_user_id_for_log(user_id),
            )
            from routes.webhook.handlers.registration import handle_patient_identity
            response = handle_patient_identity(user_id, params, query_text)
        else:
            from routes.webhook.handlers.symptoms import handle_request_appointment
            response = handle_request_appointment(user_id, params)
    elif intent == 'GetKnowledge':
        response = handle_get_knowledge(user_id, params, query_text)
    elif intent == 'GetFollowUpSummary':
        response = handle_get_followup_summary(user_id)
    elif intent == 'ContactNurse':
        response = handle_contact_nurse(user_id, params, query_text)
    elif intent == 'AfterHoursChoice':
        response = handle_after_hours_choice(user_id, query_text)
    elif intent == 'CancelConsultation':
        response = handle_cancel_consultation(user_id)
    elif intent == 'GetGroupID':
        response = handle_get_group_id()
    elif intent == 'FreeTextSymptom':
        response = handle_free_text_symptom(user_id, params, query_text)
    elif intent == 'RecommendKnowledge':
        response = handle_recommend_knowledge(user_id, params)
    elif intent in (
        'UpdatePatientIdentity',
        'PatientIdentity',
        'RegisterPatient',
        'PatientIdentity_Input',
        'PatientIdentity_Fallback',
    ):
        from routes.webhook.helpers import _registration_intent_looks_like_knowledge
        if _registration_intent_looks_like_knowledge(intent, params, query_text):
            logger.info(
                "Rerouting %s -> GetKnowledge (query=%r first_name=%r)",
                intent,
                query_text,
                (params or {}).get("first_name"),
            )
            response = handle_get_knowledge(user_id, params, query_text)
            _touch_activity("GetKnowledge", user_id)
            return response
        from services.patient_profile import is_registration_trigger_text, mark_registration_started
        if intent == "RegisterPatient" or is_registration_trigger_text(query_text):
            mark_registration_started(user_id)
        response = handle_patient_identity(user_id, params, query_text)
    else:
        response = handle_unknown_intent(intent)
    _touch_activity(intent, user_id)
    return response


def handle_line_image_event(event):
    """Process a single LINE image event end-to-end."""
    from services.notification import (
        build_wound_alert_message,
        build_wound_user_reply,
        download_line_content,
        reply_line_message,
        send_line_push,
    )
    from services.wound_analysis import analyze_wound_image
    from database.wound_logs import save_wound_analysis

    source = event.get("source") or {}
    user_id = source.get("userId") or "unknown"
    reply_token = event.get("replyToken") or ""
    msg = event.get("message") or {}
    message_id = msg.get("id") or ""

    masked_user = (user_id[:4] + "***" + user_id[-4:]) if len(user_id) > 10 else "***"
    logger.info("LINE image event user=%s message_id=%s", masked_user, message_id)

    if not message_id:
        if reply_token:
            reply_line_message(reply_token, "ไม่พบรหัสรูปภาพ กรุณาส่งใหม่อีกครั้ง")
        return

    # 1. Download
    image_bytes = download_line_content(message_id)
    if not image_bytes:
        if reply_token:
            reply_line_message(
                reply_token,
                "ขออภัย ไม่สามารถดาวน์โหลดรูปได้ในขณะนี้\nกรุณาลองส่งใหม่ในอีกสักครู่",
            )
        return

    # 2. Analyze
    result = analyze_wound_image(image_bytes, mime_type="image/jpeg")
    if not result:
        if reply_token:
            reply_line_message(
                reply_token,
                "📸 ได้รับรูปแล้ว\nระบบ AI กำลังบำรุงรักษา พยาบาลจะตรวจสอบรูปและติดต่อกลับ",
            )
        if NURSE_GROUP_ID:
            try:
                from services.notification import _get_patient_prefix_label
                patient_label = _get_patient_prefix_label(user_id)
                send_line_push(
                    f"📸 ผู้ป่วยส่งรูปแผล (AI ไม่พร้อม)\n"
                    f"👤 ผู้ป่วย: {patient_label}\n"
                    f"🆔 User ID: {user_id}\n"
                    f"กรุณาตรวจสอบรูปใน LINE",
                    NURSE_GROUP_ID,
                )
            except Exception:
                logger.exception("Failed to push raw wound notice")
        return

    # 3. Persist
    try:
        save_wound_analysis(
            user_id=user_id,
            severity=result["severity"],
            observations=result["observations"],
            advice=result["advice"],
            confidence=result["confidence"],
            image_size_kb=len(image_bytes) // 1024,
            message_id=message_id,
        )
    except Exception:
        logger.exception("Failed to persist wound analysis user=%s", user_id)

    # 4. Reply to user
    if reply_token:
        from config import ENABLE_RICH_MESSAGES
        if ENABLE_RICH_MESSAGES:
            try:
                from services.line_message import build_wound_flex_result, reply_rich_message
                flex_msg = build_wound_flex_result(
                    severity=result["severity"],
                    observations=result["observations"],
                    advice=result["advice"],
                    confidence=result["confidence"],
                )
                reply_rich_message(reply_token, [flex_msg])
            except Exception:
                logger.exception("Failed to reply with flex result, falling back to text")
                reply_line_message(
                    reply_token,
                    build_wound_user_reply(
                        severity=result["severity"],
                        observations=result["observations"],
                        advice=result["advice"],
                    ),
                )
        else:
            reply_line_message(
                reply_token,
                build_wound_user_reply(
                    severity=result["severity"],
                    observations=result["observations"],
                    advice=result["advice"],
                ),
            )

    # 5. Alert nurse if medium or high
    if result["severity"] in ("medium", "high") and NURSE_GROUP_ID:
        try:
            send_line_push(
                build_wound_alert_message(
                    user_id=user_id,
                    severity=result["severity"],
                    observations=result["observations"],
                    advice=result["advice"],
                    confidence=result["confidence"],
                ),
                NURSE_GROUP_ID,
            )
        except Exception:
            logger.exception("Failed to push wound alert user=%s", user_id)


def handle_ai_mode_intercept(user_id: str, profile: dict, query_text: str, intent: str = None):
    """Intercept incoming user text and handle AI consultation mode if active or triggered."""
    if not isinstance(query_text, str):
        return None

    cleaned = query_text.strip().lower().replace(" ", "").replace("  ", "")
    activation_keywords = {"คุยกับเอไอ", "โหมดเอไอ", "เปิดเอไอ", "ปรึกษาเอไอ", "คุยกับai", "โหมดai"}
    deactivation_keywords = {"ออกจากเอไอ", "ปิดเอไอ", "คุยกับพยาบาล", "ยกเลิก", "ออกจากai", "ปิดai"}

    # 1. Activation
    if cleaned in activation_keywords:
        from database.patient_profile import upsert_patient_profile
        from services.patient_profile import invalidate_profile_cache
        from routes.webhook.helpers import _make_dialogflow_response
        
        try:
            upsert_patient_profile(user_id, {"ai_mode": True})
            invalidate_profile_cache(user_id)
        except Exception:
            logger.exception("Failed to update ai_mode for activation user=%s", user_id)
            
        reply = (
            "🤖 ยินดีต้อนรับเข้าสู่โหมดคุยกับ AI (พยาบาลขวัญใจ AI) ค่ะ!\n"
            "คุณสามารถสอบถามเรื่องสุขภาพ การดูแลแผล หรือยาได้เลยค่ะ\n\n"
            "💡 หากต้องการออกจากโหมดนี้ พิมพ์คำว่า 'ออกจากเอไอ' หรือ 'คุยกับพยาบาล' ได้ทุกเมื่อค่ะ"
        )
        return jsonify(_make_dialogflow_response(reply)), 200

    # 2. Deactivation
    if cleaned in deactivation_keywords and profile.get("ai_mode") is True:
        from database.patient_profile import upsert_patient_profile
        from services.patient_profile import invalidate_profile_cache
        from routes.webhook.helpers import _make_dialogflow_response
        
        try:
            upsert_patient_profile(user_id, {"ai_mode": False})
            invalidate_profile_cache(user_id)
        except Exception:
            logger.exception("Failed to update ai_mode for deactivation user=%s", user_id)
            
        reply = (
            "👋 ออกจากโหมดคุยกับ AI เรียบร้อยแล้วค่ะ\n"
            "บอทจะกลับมาทำงานตามระบบปกติและติดต่อพยาบาลหากท่านต้องการค่ะ"
        )
        return jsonify(_make_dialogflow_response(reply)), 200

    # 3. Intercept and reply via LLM
    if profile.get("ai_mode") is True:
        from services.llm import complete
        from routes.webhook.helpers import _make_dialogflow_response
        
        _AI_MODE_SYSTEM_PROMPT = (
            "คุณคือ 'พยาบาลขวัญใจ AI' ผู้ช่วยดูแลผู้ป่วยหลังผ่าตัดอย่างเป็นกันเองและใส่ใจ. "
            "ให้คำแนะนำเรื่องสุขภาพ การดูแลแผลผ่าตัด และการรับประทานยาอย่างถูกต้อง ปลอดภัย และสุภาพในภาษาไทย. "
            "หากผู้ป่วยมีอาการวิกฤต รุนแรง (เช่น หายใจลำบาก เลือดไหลไม่หยุด แผลติดเชื้อรุนแรง) "
            "ให้แนะนำให้โทร 1669 หรือติดต่อโรงพยาบาลทันทีอย่างชัดเจน. "
            "ตอบสั้น กระชับ เข้าใจง่าย หลีกเลี่ยงศัพท์แพทย์ที่ยากเกินไป."
        )
        
        reply_text = complete(_AI_MODE_SYSTEM_PROMPT, query_text, intent=intent)
        if not reply_text:
            reply_text = (
                "🤖 ขณะนี้ AI มีผู้ใช้งานจำนวนมากและไม่สามารถตอบคำถามได้ชั่วคราว\n"
                "กรุณาลองใหม่อีกครั้ง หรือพิมพ์ 'คุยกับพยาบาล' เพื่อติดต่อพยาบาลโดยตรงค่ะ"
            )
        return jsonify(_make_dialogflow_response(reply_text)), 200

    return None
