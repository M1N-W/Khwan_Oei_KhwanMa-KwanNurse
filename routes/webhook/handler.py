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


def _mask_user_id_for_log(user_id):
    return scrub_user_id(user_id)


def register_routes(app):
    """Register all webhook routes with Flask app"""
    
    @app.route('/', methods=['GET', 'HEAD'])
    def health_check():
        """Health check endpoint for monitoring services with full configuration status (v5.0)"""
        from config import validate_runtime_config
        config_status = validate_runtime_config()
        
        return jsonify({
            "status": "ok" if config_status["ok"] else "warning",
            "service": "KwanNurse-Bot v5.0",
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
            params = req.get('queryResult', {}).get('parameters', {}) or {}
            user_id = req.get('session', 'unknown').split('/')[-1]
            query_text = req.get('queryResult', {}).get('queryText', '')
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
                if event.get("type") != "message":
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
    elif intent in ('UpdatePatientIdentity', 'PatientIdentity', 'RegisterPatient'):
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
                send_line_push(
                    f"📸 ผู้ป่วยส่งรูปแผล (AI ไม่พร้อม)\n👤 User: {user_id}\n"
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
