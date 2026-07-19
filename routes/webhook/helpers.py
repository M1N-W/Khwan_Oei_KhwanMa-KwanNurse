# -*- coding: utf-8 -*-
"""
Helper utilities and constants for webhook handlers (KWN-09).
"""
import time
from datetime import datetime
from flask import jsonify
from config import get_logger, LOCAL_TZ
from utils.pii import scrub_user_id

logger = get_logger(__name__)

_REGISTRATION_GATED_INTENTS = {
    "AssessRisk",
    "AssessPersonalRisk",
    "RequestAppointment",
    "GetFollowUpSummary",
    "RecommendKnowledge",
    "ReportSymptoms",
    "GetKnowledge",
    "ContactNurse",
    "FreeTextSymptom",
    "CancelConsultation",
}

_LAST_ACTIVE_TRACKED_INTENTS = {
    "ReportSymptoms",
    "AssessRisk",
    "AssessPersonalRisk",
    "RequestAppointment",
    "GetKnowledge",
    "GetFollowUpSummary",
    "ContactNurse",
    "AfterHoursChoice",
    "CancelConsultation",
    "FreeTextSymptom",
    "RecommendKnowledge",
}

_REGISTRATION_INTENTS = {
    "StartRegistration",
    "PatientIdentity",
    "UpdatePatientIdentity",
    "RegisterPatient",
    "PatientIdentity_Input",
    "PatientIdentity_Fallback",
}

def _mask_user_id_for_log(user_id):
    return scrub_user_id(user_id)


def _touch_activity(intent, user_id):
    if intent not in _LAST_ACTIVE_TRACKED_INTENTS or not user_id:
        return
    try:
        from services.patient_profile import touch_last_active
        touch_last_active(user_id)
    except Exception:
        logger.exception("activity touch failed user=%s", _mask_user_id_for_log(user_id))


def _registration_gate_response(intent, user_id, query_text):
    if intent not in _REGISTRATION_GATED_INTENTS:
        return None
    if intent == "RequestAppointment":
        if _appointment_during_registration_should_reroute(user_id, {}, query_text):
            return None
    try:
        import config as app_config
        if not app_config.PATIENT_REGISTRATION_GATE_ENABLED:
            return None
        from services.i18n import detect_language, t
        from services.patient_profile import should_prompt_registration

        decision = should_prompt_registration(user_id)
        if decision.prompt:
            lang = detect_language(query_text or "")
            return jsonify({"fulfillmentText": t("identity.incomplete_prompt", lang)}), 200
        if decision.reason == "storage_unavailable":
            logger.warning("registration gate fail-open user=%s", _mask_user_id_for_log(user_id))
    except Exception:
        logger.exception("registration gate failed user=%s", _mask_user_id_for_log(user_id))
    return None



def _appointment_during_registration_should_reroute(user_id, params, query_text):
    """
    RequestAppointment follow-up context can swallow registration name turns.
    Reroute name-like replies back to PatientIdentity while registration is incomplete.
    """
    try:
        from database.patient_profile import read_patient_profile_result
        from routes.webhook.handlers.fallback import _resolve_knowledge_topic
        from services.patient_profile import (
            enrich_registration_params,
            is_registration_trigger_text,
            normalize_identity_fields,
            registration_missing_fields,
        )

        read_result = read_patient_profile_result(user_id)
        if not read_result.available:
            return False
        missing = registration_missing_fields(read_result.profile)
        if not any(field in missing for field in ("first_name", "last_name", "hn", "phone")):
            return False

        params = params or {}
        if any(params.get(key) for key in ("date", "preferred_date", "time", "preferred_time", "reason")):
            return False

        text = (query_text or "").strip()
        if not text or is_registration_trigger_text(text):
            return False
        if _resolve_knowledge_topic(text):
            return False

        enriched = enrich_registration_params(read_result.profile, params, query_text)
        return bool(normalize_identity_fields(enriched) or enriched.get("phone"))
    except Exception:
        logger.exception(
            "appointment registration reroute check failed user=%s",
            _mask_user_id_for_log(user_id),
        )
        return False

def _registration_intent_looks_like_knowledge(intent, params, query_text):
    """Reroute misclassified knowledge topics during registration slot-filling."""
    if intent not in _REGISTRATION_INTENTS:
        return False
    from routes.webhook.handlers.fallback import (
        _KNOWLEDGE_MENU_TRIGGERS,
        _resolve_knowledge_topic,
    )
    norm_q = (query_text or "").lower().strip()
    if norm_q in _KNOWLEDGE_MENU_TRIGGERS:
        return True
    for candidate in (query_text, (params or {}).get("first_name")):
        if candidate and _resolve_knowledge_topic(str(candidate)):
            return True
    return False


def _is_active_flow_context(context: dict) -> bool:
    return (
        isinstance(context, dict)
        and int(context.get("lifespanCount", 0) or 0) > 0
        and any(name in str(context.get("name", "")) for name in (
            "reportsymptoms_dialog_context",
            "assessrisk_dialog_context",
            "assesspersonalrisk_dialog_context",
            "requestappointment_dialog_context",
            "teleconsult_category_context",
            "registering",
        ))
    )


def _with_cancel_quick_reply(quick_replies: list[dict] | None) -> list[dict]:
    """Add one escape hatch without duplicating it or exceeding LINE's cap."""
    from services.line_message import MAX_QUICK_REPLY_ITEMS, quick_reply_item

    items = list(quick_replies or [])
    if any((item.get("action") or {}).get("text") == "ยกเลิก" for item in items):
        return items[:MAX_QUICK_REPLY_ITEMS]
    return (items[:MAX_QUICK_REPLY_ITEMS - 1] + [quick_reply_item("✕ ยกเลิก", "ยกเลิก")])


def _should_show_cancel_hint(output_contexts: list[dict] | None) -> bool:
    """Show the explanatory hint only when a flow starts, not on every turn."""
    if not any(_is_active_flow_context(context) for context in (output_contexts or [])):
        return False
    try:
        from flask import has_request_context, request
        if not has_request_context():
            return True
        incoming = (request.get_json(silent=True, force=True) or {}).get("queryResult", {}).get("outputContexts", [])
        return not any(_is_active_flow_context(context) for context in incoming)
    except Exception:
        return False


def _make_dialogflow_response(text: str, quick_replies: list[dict] = None, flex_message: dict = None, output_contexts: list[dict] = None) -> dict:
    """Build a Dialogflow response, optionally including LINE custom payloads and output contexts (KWN-06)."""
    has_active_flow = any(_is_active_flow_context(context) for context in (output_contexts or []))
    if has_active_flow:
        quick_replies = _with_cancel_quick_reply(quick_replies)
        if _should_show_cancel_hint(output_contexts):
            text = text.rstrip() + "\n\n💡 ระหว่างทำรายการ กด “✕ ยกเลิก” หรือพิมพ์ “ยกเลิก” ได้ทุกเมื่อค่ะ"

    res = {"fulfillmentText": text}
    
    from config import ENABLE_RICH_MESSAGES
    if ENABLE_RICH_MESSAGES:
        if flex_message:
            res["fulfillmentMessages"] = [
                {
                    "platform": "LINE",
                    "payload": {
                        "line": flex_message
                    }
                },
                {
                    "text": {
                        "text": [text]
                    }
                }
            ]
        elif quick_replies:
            from services.line_message import build_quick_reply_message
            line_msg = build_quick_reply_message(text, quick_replies)
            res["fulfillmentMessages"] = [
                {
                    "platform": "LINE",
                    "payload": {
                        "line": line_msg
                    }
                },
                {
                    "text": {
                        "text": [text]
                    }
                }
            ]

    if output_contexts:
        res["outputContexts"] = output_contexts

    return res
