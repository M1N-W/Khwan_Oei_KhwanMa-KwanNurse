# -*- coding: utf-8 -*-
"""Services package"""
from .notification import send_line_push
from .risk_assessment import calculate_symptom_risk, calculate_personal_risk
from .appointment import create_appointment
from .knowledge import (
    get_knowledge_menu,
    get_wound_care_guide,
    get_physical_therapy_guide,
    get_dvt_prevention_guide,
    get_medication_guide,
    get_warning_signs_guide
)
from .reminder import (
    schedule_follow_up_reminders,
    send_reminder,
    handle_reminder_response,
    check_and_alert_no_response,
    get_reminder_summary
)
from .scheduler import (
    init_scheduler,
    schedule_reminder_job,
    cancel_reminder_job,
    get_scheduled_jobs,
    get_scheduler_status,
    reschedule_all_reminders
)
from .teleconsult import (
    is_office_hours,
    get_category_menu,
    parse_category_choice,        # Bug #6 fix: was missing
    start_teleconsult,
    cancel_consultation,
    get_queue_info_message,
    handle_after_hours_choice     # expose new handler
)
from .nlp import analyze_free_text, format_triage_message
from .education import recommend_guides, format_recommendations_message

__all__ = [
    'send_line_push',
    'calculate_symptom_risk',
    'calculate_personal_risk',
    'create_appointment',
    'get_knowledge_menu',
    'get_wound_care_guide',
    'get_physical_therapy_guide',
    'get_dvt_prevention_guide',
    'get_medication_guide',
    'get_warning_signs_guide',
    'schedule_follow_up_reminders',
    'send_reminder',
    'handle_reminder_response',
    'check_and_alert_no_response',
    'get_reminder_summary',
    'init_scheduler',
    'schedule_reminder_job',
    'cancel_reminder_job',
    'get_scheduled_jobs',
    'get_scheduler_status',
    'reschedule_all_reminders',
    'is_office_hours',
    'get_category_menu',
    'parse_category_choice',      # Bug #6 fix
    'start_teleconsult',
    'cancel_consultation',
    'get_queue_info_message',
    'handle_after_hours_choice',  # new handler
    # Phase 2
    'analyze_free_text',
    'format_triage_message',
    'recommend_guides',
    'format_recommendations_message',
]
