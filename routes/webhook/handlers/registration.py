# -*- coding: utf-8 -*-
"""
Intent handlers for patient registration and identity (KWN-09).
"""
from datetime import datetime
from flask import jsonify
from config import get_logger, LOCAL_TZ
from routes.webhook.helpers import _make_dialogflow_response, _mask_user_id_for_log

logger = get_logger(__name__)


def handle_patient_identity(user_id, params, query_text=""):
    """Collect/update patient registration fields incrementally."""
    try:
        from database.patient_profile import read_patient_profile_result, upsert_patient_profile
        from services.i18n import detect_language, t
        from services.patient_profile import (
            extract_explicit_consent,
            invalidate_profile_cache,
            mark_last_active_throttled,
            mask_phone_number,
            normalize_identity_fields,
            enrich_registration_params,
            prepare_registration_update,
            build_registration_quick_replies,
            build_profile_flex_summary,
        )
        from services.dashboard_readers import invalidate_dashboard_cache

        lang = detect_language(query_text or " ".join(str(v) for v in (params or {}).values()))
        read_result = read_patient_profile_result(user_id)
        if not read_result.available:
            return jsonify({"fulfillmentText": t("identity.storage_unavailable", lang)}), 200

        existing = read_result.profile
        if existing is None:
            existing = {
                "first_name": "",
                "last_name": "",
                "hn": "",
                "phone": "",
                "consent_granted": False,
                "registration_status": "incomplete"
            }
            upsert_patient_profile(user_id, existing)
            invalidate_profile_cache(user_id)
        params = enrich_registration_params(existing, params, query_text)
        update = prepare_registration_update(existing, params)
        merged = update.profile
        identity = normalize_identity_fields(params)
        phone_param_present = bool(params and any(
            key in params for key in ("phone", "phone_number", "phone-number", "tel")
        ))
        consent_seen = extract_explicit_consent(params) is not None
        should_save = bool(identity or phone_param_present or consent_seen)

        if should_save:
            merged["last_active_at"] = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
            ok = upsert_patient_profile(user_id, merged)
            if not ok:
                return jsonify({"fulfillmentText": t("identity.save_error", lang)}), 200
            mark_last_active_throttled(user_id)
            invalidate_profile_cache(user_id)
            invalidate_dashboard_cache()

        first_name = merged.get("first_name") or ""
        last_name = merged.get("last_name") or ""
        hn = merged.get("hn") or ""
        phone = merged.get("phone") or ""

        # Gather remaining missing fields for quick replies
        missing_fields = update.missing_fields

        # Set Dialogflow output context for registration to intercept turns
        output_contexts = None
        if missing_fields:
            from flask import has_request_context, request as flask_req
            if has_request_context():
                req_json = flask_req.get_json(silent=True, force=True) or {}
                session = req_json.get("session")
                if session:
                    output_contexts = [{
                        "name": f"{session}/contexts/registering",
                        "lifespanCount": 5
                    }]

        if not first_name:
            return jsonify(_make_dialogflow_response(t("identity.ask_first_name", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200
        if not last_name:
            return jsonify(_make_dialogflow_response(t("identity.ask_last_name", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200
        if not hn:
            return jsonify(_make_dialogflow_response(t("identity.ask_hn", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200
        if "phone" in update.invalid_fields:
            return jsonify(_make_dialogflow_response(t("identity.invalid_phone", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200
        if not phone:
            return jsonify(_make_dialogflow_response(t("identity.ask_phone", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200
        if update.consent_declined:
            return jsonify(_make_dialogflow_response(t("identity.consent_declined", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200
        if "consent" in missing_fields:
            return jsonify(_make_dialogflow_response(t("identity.ask_consent", lang), build_registration_quick_replies(missing_fields), output_contexts=output_contexts)), 200

        # All registered successfully
        confirm_text = t(
            "identity.confirm",
            lang,
            first_name=first_name,
            last_name=last_name,
            hn=hn,
            phone=mask_phone_number(phone),
        )
        flex_summary = build_profile_flex_summary(merged)
        
        try:
            from services.survey import schedule_milestone_surveys
            schedule_milestone_surveys(user_id)
        except Exception:
            logger.exception("Failed to schedule milestone surveys upon completion user=%s", user_id)
            
        # Clear registering context
        clear_contexts = None
        from flask import has_request_context, request as flask_req
        if has_request_context():
            req_json = flask_req.get_json(silent=True, force=True) or {}
            session = req_json.get("session")
            if session:
                clear_contexts = [{
                    "name": f"{session}/contexts/registering",
                    "lifespanCount": 0
                }]

        return jsonify(_make_dialogflow_response(confirm_text, flex_message=flex_summary, output_contexts=clear_contexts)), 200
    except Exception:
        logger.exception("Error in PatientIdentity handler user=%s", _mask_user_id_for_log(user_id))
        return jsonify({"fulfillmentText": "ขอโทษค่ะ ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"}), 200
