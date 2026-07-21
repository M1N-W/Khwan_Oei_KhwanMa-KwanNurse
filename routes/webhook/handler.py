# -*- coding: utf-8 -*-
"""
Webhook routing and entry point (KWN-09).
Maps incoming HTTP webhook requests and dispatches Dialogflow intents to handlers.
"""
import os
import json
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


def _has_active_teleconsult_session(user_id: str) -> bool:
    """Check persisted consultation state for fallback recovery only."""
    if not user_id:
        return False
    try:
        from database.teleconsult import get_user_active_session
        return bool(get_user_active_session(user_id))
    except Exception:
        logger.exception("Failed to inspect active teleconsult state user=%s", scrub_user_id(user_id))
        return False


def _get_clear_all_contexts(
    session: str | None,
    exclude: tuple[str, ...] = (),
) -> list[dict]:
    """Return a list of output contexts to clear all active state/slot-filling dialog contexts."""
    if not session:
        return []
    contexts_to_clear = [
        "registering",
        "reportsymptoms_dialog_context",
        "assessrisk_dialog_context",
        "assesspersonalrisk_dialog_context",
        "requestappointment_dialog_context",
        "teleconsult_category_context",
    ]
    return [
        {"name": f"{session}/contexts/{name}", "lifespanCount": 0}
        for name in contexts_to_clear
        if name not in exclude
    ]


def _handle_line_text_event(event: dict) -> None:
    """Handle text when LINE is configured directly without Dialogflow."""
    if not isinstance(event, dict):
        return
    source = event.get("source") or {}
    user_id = source.get("userId") or "unknown"
    reply_token = event.get("replyToken") or ""
    text = str((event.get("message") or {}).get("text") or "").strip()
    if not text or not reply_token:
        return

    normalized = text.casefold().replace(" ", "")
    if normalized in {"ส่งรูปแผล", "ส่งภาพแผล", "ถ่ายรูปแผล"}:
        from services.notification import reply_line_message
        reply_line_message(
            reply_token,
            "📷 กรุณาส่งรูปแผลในแชตนี้ได้เลยนะคะ ระบบจะรับรูปไปวิเคราะห์เบื้องต้นค่ะ",
        )
        return

    try:
        from services.dialogflow_bridge import detect_intent
        result = detect_intent(user_id, text)
        _reply_line_from_bridge_result(reply_token, user_id, result)
    except Exception:
        logger.exception("Direct LINE text bridge failed user=%s", scrub_user_id(user_id))
        from services.notification import reply_line_message
        reply_line_message(reply_token, "⚠️ ขออภัยค่ะ ระบบสนทนาขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้งค่ะ")


def _reply_line_from_bridge_result(reply_token: str, user_id: str, result: dict) -> None:
    """Send Dialogflow detect-intent output through the LINE Messaging API."""
    query_result = (result or {}).get("queryResult") or {}
    intent_name = ((query_result.get("intent") or {}).get("displayName") or "")

    # Direct LINE mode can send Flex safely; use it for the completed profile
    # card while the Dialogflow LINE integration remains text-only for Flex.
    if intent_name in {"PatientIdentity", "PatientIdentity_Input", "PatientIdentity_Fallback", "ViewMyProfile"}:
        try:
            from database.patient_profile import read_patient_profile_result
            from services.patient_profile import build_profile_flex_summary, is_registration_complete
            profile_result = read_patient_profile_result(user_id)
            if profile_result.available and is_registration_complete(profile_result.profile):
                from services.notification import reply_line_message_objects
                reply_line_message_objects(reply_token, [build_profile_flex_summary(profile_result.profile)])
                return
        except Exception:
            logger.exception("Failed to build direct LINE profile card user=%s", scrub_user_id(user_id))

    line_messages = []
    for item in query_result.get("fulfillmentMessages") or []:
        if item.get("platform") != "LINE":
            continue
        line_payload = (item.get("payload") or {}).get("line")
        if line_payload:
            line_messages.append(line_payload)

    from services.notification import reply_line_message, reply_line_message_objects
    if line_messages:
        reply_line_message_objects(reply_token, line_messages)
        return
    reply_line_message(reply_token, query_result.get("fulfillmentText") or "ขออภัยค่ะ กรุณาลองใหม่อีกครั้ง")


def _reply_line_from_dialogflow_result(reply_token: str, result) -> None:
    """Convert a Flask/Dialogflow handler result into a LINE reply."""
    if not reply_token:
        return
    response = result[0] if isinstance(result, tuple) else result
    payload = response.get_json(silent=True) if hasattr(response, "get_json") else {}
    payload = payload or {}
    line_messages = []
    for item in payload.get("fulfillmentMessages") or []:
        if item.get("platform") == "LINE":
            line_payload = (item.get("payload") or {}).get("line")
            if line_payload:
                line_messages.append(line_payload)
    from services.notification import reply_line_message, reply_line_message_objects
    if line_messages:
        reply_line_message_objects(reply_token, line_messages)
    else:
        reply_line_message(reply_token, payload.get("fulfillmentText") or "ขออภัยค่ะ กรุณาลองใหม่อีกครั้ง")


def _has_active_context(req: dict, context_name: str) -> bool:
    """Check if context_name is active (lifespanCount > 0) in Dialogflow request."""
    contexts = req.get('queryResult', {}).get('outputContexts', [])
    for ctx in contexts:
        name = ctx.get('name', '')
        if context_name in name:
            if ctx.get('lifespanCount', 0) > 0:
                return True
    return False


def _extract_context_parameters(req: dict, context_name: str) -> dict:
    """Extract parameters from Dialogflow context."""
    contexts = req.get('queryResult', {}).get('outputContexts', [])
    for ctx in contexts:
        name = ctx.get('name', '')
        if context_name in name:
            return ctx.get('parameters', {}) or {}
    return {}


def _clear_context_from_response(response, session: str | None, context_name: str):
    """Append a Dialogflow context clear operation without changing handler output."""
    if not session or not response:
        return response
    if isinstance(response, tuple) and isinstance(response[0], dict):
        payload = dict(response[0])
        output_contexts = list(payload.get("outputContexts") or [])
        output_contexts.append({
            "name": f"{session}/contexts/{context_name}",
            "lifespanCount": 0,
        })
        payload["outputContexts"] = output_contexts
        return (payload, *response[1:])
    if isinstance(response, dict):
        payload = dict(response)
        output_contexts = list(payload.get("outputContexts") or [])
        output_contexts.append({
            "name": f"{session}/contexts/{context_name}",
            "lifespanCount": 0,
        })
        payload["outputContexts"] = output_contexts
        return payload
    response_obj = response[0] if isinstance(response, tuple) else response
    payload = response_obj.get_json(silent=True) if hasattr(response_obj, "get_json") else None
    if not isinstance(payload, dict):
        return response
    output_contexts = list(payload.get("outputContexts") or [])
    output_contexts.append({
        "name": f"{session}/contexts/{context_name}",
        "lifespanCount": 0,
    })
    payload["outputContexts"] = output_contexts
    response_obj.set_data(json.dumps(payload, ensure_ascii=False))
    response_obj.content_type = "application/json"
    return response


def _clear_contexts_from_response(
    response,
    session: str | None,
    context_names: tuple[str, ...] | list[str],
):
    """Clear competing feature contexts while preserving the handler payload."""
    for context_name in context_names:
        response = _clear_context_from_response(response, session, context_name)
    return response


def _append_context_operations_to_response(response, operations: tuple[dict, ...] | list[dict]):
    """Append controller-owned Dialogflow context operations to any handler response."""
    if not operations or not response:
        return response
    if isinstance(response, tuple) and isinstance(response[0], dict):
        payload = dict(response[0])
        payload["outputContexts"] = list(payload.get("outputContexts") or []) + list(operations)
        return (payload, *response[1:])
    if isinstance(response, dict):
        payload = dict(response)
        payload["outputContexts"] = list(payload.get("outputContexts") or []) + list(operations)
        return payload
    response_obj = response[0] if isinstance(response, tuple) else response
    payload = response_obj.get_json(silent=True) if hasattr(response_obj, "get_json") else None
    if not isinstance(payload, dict):
        return response
    payload["outputContexts"] = list(payload.get("outputContexts") or []) + list(operations)
    response_obj.set_data(json.dumps(payload, ensure_ascii=False))
    response_obj.content_type = "application/json"
    return response


def register_routes(app):
    """Register all webhook routes with Flask app"""

    @app.errorhandler(Exception)
    def handle_global_exception(e):
        """Global error handler to prevent silent crashes and return fallback text to user."""
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
            
        logger.exception("Global exception caught: %s", e)
        
        from flask import request
        try:
            if request.path == '/webhook':
                from routes.webhook.helpers import _make_dialogflow_response
                fallback_msg = "⚠️ ขออภัยค่ะ ขณะนี้ระบบขัดข้องชั่วคราว ทีมงานกำลังเร่งแก้ไข กรุณาลองใหม่อีกครั้งในภายหลังค่ะ"
                return jsonify(_make_dialogflow_response(fallback_msg)), 200
            elif request.path == '/line/webhook':
                body = request.get_json(silent=True) or {}
                events = body.get("events") or []
                if events:
                    event = events[0]
                    reply_token = event.get("replyToken")
                    if reply_token:
                        try:
                            from services.notification import reply_line_message
                            fallback_msg = "⚠️ ขออภัยค่ะ ขณะนี้ระบบขัดข้องชั่วคราว ทีมงานกำลังเร่งแก้ไข กรุณาลองใหม่อีกครั้งในภายหลังค่ะ"
                            reply_line_message(reply_token, fallback_msg)
                        except Exception:
                            logger.exception("Failed to reply fallback message to LINE user during global exception handler")
                return jsonify({"status": "error", "message": str(e)}), 200
        except Exception as inner_ex:
            logger.exception("Error inside global exception handler: %s", inner_ex)
            
        return jsonify({"status": "error", "message": "Internal Server Error"}), 500
    
    @app.route('/', methods=['GET', 'HEAD'])
    def health_check():
        """Health check endpoint for monitoring services with full configuration status (v5.0)"""
        from config import validate_runtime_config
        config_status = validate_runtime_config()
        from services.llm import is_enabled, _resolve_model
        
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
                "can_persist_sheets": config_status["can_persist"],
                "conversation_router_ready": config_status.get("conversation_router_ready", False),
                "llm_provider": os.environ.get("LLM_PROVIDER", "none"),
                "llm_enabled": bool(is_enabled()),
                "llm_model": _resolve_model() or None,
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
            normalized_query = query_text.strip().lower() if isinstance(query_text, str) else ""
            top_level_commands = {
                "ลงทะเบียน", "register", "สมัครสมาชิก", "เข้าสู่ระบบ", "สมัคร", "ขอยืนยันตัวตน", "ลงทะเบียนผู้ป่วย", "ต้องการลงทะเบียน",
                "ข้อมูลของฉัน", "ดูบัตรผู้ป่วย", "แก้ไขข้อมูล",
                "รายงานอาการ", "แจ้งอาการ", "ประเมินความเสี่ยง", "ประเมินความเสี่ยงส่วนบุคคล",
                "นัดหมายพยาบาล", "นัดหมาย", "ความรู้", "เมนูความรู้", "เมนูความรู้หลัก", "คู่มือ",
                "ติดตามหลังให้ยา", "ติดตามอาการ", "ปรึกษาพยาบาล", "ติดต่อพยาบาล", "คุยกับพยาบาล",
                "ส่งรูปแผล", "ส่งภาพแผล", "ถ่ายรูปแผล",
            }
            is_top_level_command = normalized_query in top_level_commands
            is_explicit_command = is_top_level_command
            is_ai_control_command = normalized_query in {
                "คุยกับเอไอ", "โหมดเอไอ", "เปิดเอไอ", "ปรึกษาเอไอ", "คุยกับai", "โหมดai",
                "ออกจากเอไอ", "ปิดเอไอ", "ออกจากai", "ปิดai", "คุยกับพยาบาล", "ยกเลิก", "ออก",
            }
            is_flow_command = normalized_query in {
                "รายงานอาการ", "แจ้งอาการ",
                "ประเมินความเสี่ยง", "ประเมินความเสี่ยงส่วนบุคคล",
                "นัดหมายพยาบาล", "นัดหมาย",
            }

            # Safe routing diagnostics: log intent and parameter names, never values.
            output_contexts = req.get('queryResult', {}).get('outputContexts') or []
            logger.info(
                "RAW_INTENT_DEBUG: matched=%s query_len=%d param_keys=%s active_contexts=%s",
                req.get('queryResult', {}).get('intent', {}).get('displayName'),
                len(query_text) if isinstance(query_text, str) else 0,
                sorted((req.get('queryResult', {}).get('parameters') or {}).keys()),
                [c.get('name') for c in output_contexts if isinstance(c, dict)],
            )
            # --- END SAFE ROUTING DEBUG ---

            # The state controller is authoritative when enabled.  It is
            # intentionally evaluated before any legacy context interception
            # so a durable teleconsult row can never claim another flow's digit.
            from config import CONVERSATION_FLOW_ROUTER_ENABLED
            controller_context_operations = ()
            controller_active = CONVERSATION_FLOW_ROUTER_ENABLED
            if controller_active:
                from services.conversation_router import resolve_route
                from services.conversation_state import get_conversation_state_store

                event_id = (
                    req.get("webhookEventId")
                    or (req.get("originalDetectIntentRequest", {}).get("payload", {}) or {}).get("webhookEventId")
                )
                try:
                    decision = resolve_route(
                        user_id=user_id,
                        channel_id="line",
                        query_text=query_text,
                        dialogflow_intent=intent or "Default Fallback Intent",
                        dialogflow_params=params,
                        session_name=req.get("session"),
                        webhook_event_id=event_id,
                        store=get_conversation_state_store(),
                    )
                except Exception:
                    # A state-store outage must fail closed: accepting a bare
                    # digit through the legacy router would reintroduce cross-flow routing.
                    incr("conversation.store_unavailable")
                    logger.exception("Conversation state store unavailable")
                    return jsonify({
                        "fulfillmentText": "ขออภัยค่ะ ระบบสนทนาขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้งค่ะ"
                    }), 200
                if decision.duplicate:
                    incr("conversation.duplicate_event")
                    return jsonify({"fulfillmentText": ""}), 200
                if decision.response_text is not None:
                    incr("conversation.validation_rejected")
                    from routes.webhook.helpers import _make_dialogflow_response
                    return jsonify(_make_dialogflow_response(
                        decision.response_text,
                        output_contexts=list(decision.context_operations),
                    )), 200
                intent = decision.intent
                params = decision.params
                controller_context_operations = decision.context_operations
                if decision.state:
                    incr(f"conversation.route.{decision.state.flow_id}.{decision.state.step_id or 'complete'}")
                # Do not let AI interception interpret a state-owned slot.
                is_flow_command = bool(decision.state or intent in {
                    "ReportSymptoms", "AssessRisk", "RequestAppointment", "AfterHoursChoice", "PatientIdentity", "StartRegistration",
                })

            # Context-based Intent Interception (Component 4)
            is_cancel_or_nurse = False
            if isinstance(query_text, str):
                is_cancel_or_nurse = query_text.strip().lower() in (
                    "ยกเลิก", "ยกเลิกคำขอ", "ยกเลิกปรึกษา", "ยกเลิกการลงทะเบียน", 
                    "ยกเลิกนัด", "ยกเลิกนัดหมาย", "ปรึกษาพยาบาล", "ติดต่อพยาบาล", "คุยกับพยาบาล",
                    "ออก", "ออกจากขั้นตอน", "exit", "cancel"
                )

            if not is_cancel_or_nurse:
                is_teleconsult_digit = (
                    query_text.strip() in {"1", "2", "3", "4", "5"}
                    and _has_active_context(req, "teleconsult_category_context")
                )
                if (
                    _has_active_context(req, 'requestappointment_dialog_context')
                    and not is_teleconsult_digit
                    and not is_top_level_command
                    and intent != 'RequestAppointment'
                ):
                    ctx_params = _extract_context_parameters(req, 'requestappointment_dialog_context')
                    new_params = dict(ctx_params)
                    # Bug #3 (interception layer): same smart-merge — context wins
                    # over Dialogflow's fresh params to prevent @sys.date from
                    # overwriting already-collected apt_day/month/year slots.
                    for k, v in (params or {}).items():
                        if k not in new_params or not new_params.get(k):
                            new_params[k] = v
                    params = new_params
                    intent = 'RequestAppointment'
                    logger.info(
                        "Intent hijacked and rerouted to RequestAppointment: query_len=%d",
                        len(query_text) if isinstance(query_text, str) else 0,
                    )
                elif (
                    not is_top_level_command
                    and intent not in {"AssessRisk", "AssessPersonalRisk"}
                    and (
                        _has_active_context(req, "assessrisk_dialog_context")
                        or _has_active_context(req, "assesspersonalrisk_dialog_context")
                    )
                ):
                    # Runtime owns an active risk-assessment flow; do not let a
                    # numeric answer be reclassified as a teleconsult choice.
                    context_name = (
                        "assesspersonalrisk_dialog_context"
                        if _has_active_context(req, "assesspersonalrisk_dialog_context")
                        else "assessrisk_dialog_context"
                    )
                    ctx_params = _extract_context_parameters(req, context_name)
                    new_params = dict(ctx_params)
                    for key, value in (params or {}).items():
                        if key not in new_params or not new_params.get(key):
                            new_params[key] = value
                    if isinstance(query_text, str) and query_text.strip():
                        for slot in ("age", "weight", "height", "disease", "diseases"):
                            if not str(new_params.get(slot) or "").strip():
                                new_params[slot] = query_text.strip()
                                break
                    params = new_params
                    intent = "AssessPersonalRisk" if context_name.startswith("assesspersonal") else "AssessRisk"
                    logger.info(
                        "Intent rerouted to active risk flow context=%s query_len=%d",
                        context_name,
                        len(query_text) if isinstance(query_text, str) else 0,
                    )
                elif _has_active_context(req, 'reportsymptoms_dialog_context'):
                    # Recognized commands interrupt slot filling; only
                    # fallback-like intents may consume the next symptom slot.
                    interrupting_intents = {
                        'CancelConsultation', 'GetKnowledge', 'ContactNurse',
                        'RequestAppointment', 'AssessRisk', 'AssessPersonalRisk',
                        'ReportSymptoms', 'Teleconsult', 'AfterHoursChoice',
                    }
                    if intent not in interrupting_intents:
                        ctx_params = _extract_context_parameters(req, 'reportsymptoms_dialog_context')
                        new_params = dict(ctx_params)
                        new_params.update(params)
                        if not new_params.get('pain_score'):
                            new_params['pain_score'] = query_text
                        elif not new_params.get('wound_status'):
                            new_params['wound_status'] = query_text
                        elif not new_params.get('fever_check'):
                            new_params['fever_check'] = query_text
                        elif not new_params.get('mobility_status'):
                            new_params['mobility_status'] = query_text
                        params = new_params
                        intent = 'ReportSymptoms'
                        logger.info(
                            "Intent hijacked and rerouted to ReportSymptoms: query_len=%d",
                            len(query_text) if isinstance(query_text, str) else 0,
                        )
            
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
                if cleaned_query in (
                    "ยกเลิก", "ยกเลิกคำขอ", "ยกเลิกปรึกษา", "ยกเลิกการลงทะเบียน", 
                    "ยกเลิกนัด", "ยกเลิกนัดหมาย", "ออก", "ออกจากขั้นตอน", "exit", "cancel"
                ):
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
                        # Only cancel a durable nurse request when the active flow is
                        # teleconsult (or there is no form in progress). Cancelling a
                        # symptom/risk/appointment form must not remove a nurse queue.
                        active_non_teleconsult_flow = any(
                            _has_active_context(req, context_name)
                            for context_name in (
                                "reportsymptoms_dialog_context",
                                "assessrisk_dialog_context",
                                "assesspersonalrisk_dialog_context",
                                "requestappointment_dialog_context",
                            )
                        )
                        teleconsult_cancelled = False
                        if not active_non_teleconsult_flow:
                            try:
                                from services.teleconsult import cancel_consultation
                                tc_result = cancel_consultation(user_id)
                                if tc_result and tc_result.get('success'):
                                    teleconsult_cancelled = True
                                    logger.info("Teleconsult session cancelled for user %s", user_id)
                            except Exception:
                                logger.warning("Failed to cancel teleconsult session for user %s", user_id, exc_info=True)
                        msg = (
                            "❌ ยกเลิกคำขอปรึกษาและนำออกจากคิวเรียบร้อยแล้วค่ะ"
                            if teleconsult_cancelled
                            else "❌ ยกเลิกการทำรายการนี้แล้วค่ะ ข้อมูลที่ยังไม่บันทึกจะไม่ถูกส่ง"
                        )
                    from routes.webhook.helpers import _make_dialogflow_response
                    return jsonify(_make_dialogflow_response(msg, output_contexts=clear_contexts)), 200

                # The wound-photo command contains "แผล", which is also a
                # knowledge topic. Resolve it before AI/knowledge interception.
                if cleaned_query in ("ส่งรูปแผล", "ส่งภาพแผล", "ถ่ายรูปแผล"):
                    intent = "RequestWoundImage"
                    params = {}

                # AI Mode intercept (only for registered patients)
                if (
                    intent != "RequestWoundImage"
                    and not is_flow_command
                    and (not is_explicit_command or is_ai_control_command)
                    and read_result
                    and read_result.available
                    and read_result.profile
                ):
                    profile = read_result.profile
                    from services.patient_profile import registration_missing_fields
                    missing = registration_missing_fields(profile)
                    if not missing:
                        ai_response = handle_ai_mode_intercept(user_id, profile, query_text, intent=intent)
                        if ai_response is not None:
                            return ai_response

                # Core keyword routing
                if cleaned_query in ("ลงทะเบียน", "register", "สมัครสมาชิก", "เข้าสู่ระบบ", "สมัคร", "ขอยืนยันตัวตน", "ลงทะเบียนผู้ป่วย", "ต้องการลงทะเบียน"):
                    intent = "StartRegistration"
                    params = {}
                elif cleaned_query in ("ข้อมูลของฉัน", "ดูบัตรผู้ป่วย"):
                    intent = "ViewMyProfile"
                    params = {}
                elif cleaned_query in ("แก้ไขข้อมูล", "แก้ไขประวัติ"):
                    intent = "EditMyProfile"
                    params = {}
                elif cleaned_query in ("รายงานอาการ", "แจ้งอาการ"):
                    intent = "ReportSymptoms"
                    params = {}
                elif cleaned_query in ("ประเมินความเสี่ยง", "ประเมินความเสี่ยงส่วนบุคคล"):
                    intent = "AssessRisk"
                    params = {}
                elif cleaned_query in ("นัดหมายพยาบาล", "นัดหมาย"):
                    intent = "RequestAppointment"
                    params = {}
                elif cleaned_query in ("ความรู้", "เมนูความรู้", "เมนูความรู้หลัก", "คู่มือ"):
                    intent = "GetKnowledge"
                    params = {}
                elif cleaned_query in ("ติดตามหลังให้ยา", "ติดตามอาการ", "ติดตามหลังจำหน่าย", "ติดตาม"):
                    intent = "GetFollowUpSummary"
                    params = {}
                elif cleaned_query in ("ปรึกษาพยาบาล", "ติดต่อพยาบาล", "คุยกับพยาบาล"):
                    intent = "ContactNurse"
                    params = {}
                elif cleaned_query in ("ยกเลิก", "ยกเลิกคำขอ", "ยกเลิกปรึกษา"):
                    intent = "CancelConsultation"
                elif cleaned_query in ("แจ้งเรื่องฉุกเฉิน", "รอเวลาทำการ"):
                    intent = "AfterHoursChoice"
                elif (
                    not controller_active
                    and not is_cancel_or_nurse
                    and cleaned_query in {"1", "2", "3", "4", "5"}
                    and (
                        _has_active_context(req, "teleconsult_category_context")
                        or (
                            intent == "Default Fallback Intent"
                            and _has_active_teleconsult_session(user_id)
                        )
                    )
                ):
                    # Dialogflow has no reliable context filter for bare menu digits.
                    # Keep the active consultation menu deterministic at the webhook boundary.
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
                        if first_missing == "name":
                            params = {"first_name": query_text}
                        elif first_missing == "hn":
                            params = {"hn": query_text}
                        elif first_missing == "citizen_id":
                            params = {"citizen_id": query_text}
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
            response = _dispatch_intent(intent, user_id, params, query_text)
            response = _append_context_operations_to_response(response, controller_context_operations)
            if is_top_level_command:
                active_context = {
                    "ReportSymptoms": "reportsymptoms_dialog_context",
                    "AssessRisk": "assessrisk_dialog_context",
                    "AssessPersonalRisk": "assesspersonalrisk_dialog_context",
                    "RequestAppointment": "requestappointment_dialog_context",
                    "ContactNurse": "teleconsult_category_context",
                }.get(intent)
                if active_context:
                    competing_contexts = tuple(
                        name for name in (
                            "reportsymptoms_dialog_context",
                            "assessrisk_dialog_context",
                            "assesspersonalrisk_dialog_context",
                            "requestappointment_dialog_context",
                            "teleconsult_category_context",
                        )
                        if name != active_context
                    )
                    response = _clear_contexts_from_response(
                        response,
                        req.get("session"),
                        competing_contexts,
                    )
            return response
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
                elif msg_type == "text":
                    # Dialogflow is the production owner of text turns.  Processing
                    # the same LINE event here would consume the reply token twice.
                    from config import LINE_TEXT_BRIDGE_ENABLED
                    if LINE_TEXT_BRIDGE_ENABLED:
                        _handle_line_text_event(event)
                    else:
                        logger.debug("Ignoring raw LINE text; Dialogflow owns text routing")
            except Exception:
                logger.exception("Error processing LINE event: %s", event.get("type"))
                reply_token = event.get("replyToken")
                if reply_token:
                    try:
                        from services.notification import reply_line_message
                        fallback_msg = "⚠️ ขออภัยค่ะ ขณะนี้ระบบขัดข้องชั่วคราว ทีมงานกำลังเร่งแก้ไข กรุณาลองใหม่อีกครั้งในภายหลังค่ะ"
                        reply_line_message(reply_token, fallback_msg)
                    except Exception:
                        logger.exception("Failed to reply fallback message to LINE user during event processing crash")
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
        handle_view_patient_profile,
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
                "Rerouting RequestAppointment -> PatientIdentity (query_len=%d user=%s)",
                len(query_text) if isinstance(query_text, str) else 0,
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
    elif intent == 'RequestWoundImage':
        from routes.webhook.helpers import _make_dialogflow_response
        response = jsonify(_make_dialogflow_response(
            "📷 พร้อมแล้วค่ะ กรุณาส่งรูปแผลในแชตนี้ได้เลยนะคะ\n"
            "ระบบจะรับรูปและส่งให้ AI วิเคราะห์เบื้องต้นค่ะ"
        )), 200
    elif intent == "ViewMyProfile":
        response = handle_view_patient_profile(user_id)
    elif intent == "EditMyProfile":
        response = handle_patient_identity(user_id, {}, "แก้ไขข้อมูล")
    elif intent in (
        'StartRegistration',
        'UpdatePatientIdentity',
        'PatientIdentity',
        'RegisterPatient',
        'PatientIdentity_Input',
        'PatientIdentity_Fallback',
    ):
        from routes.webhook.helpers import _registration_intent_looks_like_knowledge
        if _registration_intent_looks_like_knowledge(intent, params, query_text):
            logger.info(
                "Rerouting %s -> GetKnowledge (query_len=%d param_keys=%s)",
                intent,
                len(query_text) if isinstance(query_text, str) else 0,
                sorted((params or {}).keys()),
            )
            response = handle_get_knowledge(user_id, params, query_text)
            _touch_activity("GetKnowledge", user_id)
            return response
        from services.patient_profile import is_registration_trigger_text, mark_registration_started
        if intent in {"StartRegistration", "RegisterPatient"} or is_registration_trigger_text(query_text):
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
