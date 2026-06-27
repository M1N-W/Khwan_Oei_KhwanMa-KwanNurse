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

_REGISTRATION_INTENTS = {"PatientIdentity", "UpdatePatientIdentity", "RegisterPatient"}


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


def _make_dialogflow_response(text: str, quick_replies: list[dict] = None, flex_message: dict = None) -> dict:
    """Build a Dialogflow response, optionally including LINE custom payloads (KWN-06)."""
    from config import ENABLE_RICH_MESSAGES
    if not ENABLE_RICH_MESSAGES:
        return {"fulfillmentText": text}

    if flex_message:
        return {
            "fulfillmentText": text,
            "fulfillmentMessages": [
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
        }

    if quick_replies:
        from services.line_message import build_quick_reply_message
        line_msg = build_quick_reply_message(text, quick_replies)
        return {
            "fulfillmentText": text,
            "fulfillmentMessages": [
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
        }

    return {"fulfillmentText": text}
