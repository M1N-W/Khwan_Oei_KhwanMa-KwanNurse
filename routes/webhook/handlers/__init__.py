# -*- coding: utf-8 -*-
"""
Handlers package (KWN-09).
Exposes intent handlers from modular sub-modules.
"""
from routes.webhook.handlers.registration import handle_patient_identity, handle_view_patient_profile
from routes.webhook.handlers.symptoms import (
    handle_report_symptoms,
    handle_assess_risk,
    handle_request_appointment,
    handle_free_text_symptom
)
from routes.webhook.handlers.reminders import (
    handle_get_followup_summary,
    handle_recommend_knowledge
)
from routes.webhook.handlers.fallback import (
    handle_get_knowledge,
    handle_contact_nurse,
    handle_cancel_consultation,
    handle_get_group_id,
    handle_unknown_intent,
    handle_after_hours_choice
)

__all__ = [
    'handle_patient_identity',
    'handle_view_patient_profile',
    'handle_report_symptoms',
    'handle_assess_risk',
    'handle_request_appointment',
    'handle_free_text_symptom',
    'handle_get_followup_summary',
    'handle_recommend_knowledge',
    'handle_get_knowledge',
    'handle_contact_nurse',
    'handle_cancel_consultation',
    'handle_get_group_id',
    'handle_unknown_intent',
    'handle_after_hours_choice'
]
