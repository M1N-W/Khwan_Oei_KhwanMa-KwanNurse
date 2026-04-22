# -*- coding: utf-8 -*-
"""
Backward-compatible shim.

The real implementation lives in `services/notification.py`. This module
used to contain a duplicate copy which caused drift risk. Keep this file
so any external caller (old Dialogflow fulfillment config, out-of-repo
scripts) that still does `from notification import send_line_push` keeps
working, but delegate everything to the canonical module.
"""
from services.notification import (  # noqa: F401
    send_line_push,
    build_symptom_notification,
    build_risk_notification,
    build_appointment_notification,
)
