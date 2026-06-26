# -*- coding: utf-8 -*-
"""
Webhook package (KWN-09).
Exposes the register_routes function and sub-handler/helper interfaces for backward-compatibility with tests.
"""
from routes.webhook.handler import register_routes, _dispatch_intent, handle_line_image_event
from routes.webhook.helpers import _touch_activity, _make_dialogflow_response, _registration_gate_response
from routes.webhook.handlers.fallback import _resolve_knowledge_topic
from routes.webhook.handlers import (
    handle_patient_identity,
    handle_report_symptoms,
    handle_assess_risk,
    handle_request_appointment,
    handle_get_knowledge,
    handle_get_followup_summary,
    handle_recommend_knowledge,
    handle_cancel_consultation,
    handle_contact_nurse,
    handle_get_group_id,
    handle_free_text_symptom,
    handle_after_hours_choice,
    handle_unknown_intent
)

# Exposed imports for tests that patch/import directly from routes.webhook
from config import DEBUG
from services.education import recommend_guides
from services.nlp import analyze_free_text

__all__ = [
    'register_routes',
    '_dispatch_intent',
    'handle_line_image_event',
    '_touch_activity',
    '_make_dialogflow_response',
    '_resolve_knowledge_topic',
    '_registration_gate_response',
    'handle_patient_identity',
    'handle_report_symptoms',
    'handle_assess_risk',
    'handle_request_appointment',
    'handle_get_knowledge',
    'handle_get_followup_summary',
    'handle_recommend_knowledge',
    'handle_cancel_consultation',
    'handle_contact_nurse',
    'handle_get_group_id',
    'handle_free_text_symptom',
    'handle_after_hours_choice',
    'handle_unknown_intent',
    'DEBUG',
    'recommend_guides',
    'analyze_free_text'
]
