"""Flask-level evidence that state-owned input cannot cross features."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _payload(text, intent, event_id):
    return {
        "session": "projects/p/agent/sessions/U1",
        "webhookEventId": event_id,
        "queryResult": {
            "queryText": text,
            "intent": {"displayName": intent},
            "parameters": {},
            "outputContexts": [{
                "name": "projects/p/agent/sessions/U1/contexts/teleconsult_category_context",
                "lifespanCount": 5,
                "parameters": {},
            }],
        },
    }


class ConversationFlowE2ETests(unittest.TestCase):
    def test_symptom_digit_cannot_be_hijacked_by_stale_teleconsult_context(self):
        from app import create_app
        from services.conversation_state import InMemoryConversationStateStore

        app = create_app()
        store = InMemoryConversationStateStore()
        with patch("config.CONVERSATION_FLOW_ROUTER_ENABLED", True), \
             patch("services.conversation_state.get_conversation_state_store", return_value=store), \
             patch("routes.webhook.handler._dispatch_intent", return_value=({"fulfillmentText": "ok"}, 200)) as dispatch:
            first = app.test_client().post("/webhook", json=_payload("รายงานอาการ", "ReportSymptoms", "evt-1"))
            second = app.test_client().post("/webhook", json=_payload("3", "AfterHoursChoice", "evt-2"))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(dispatch.call_args_list[-1].args, ("ReportSymptoms", "U1", {"pain_score": "3"}, "3"))

    def test_duplicate_webhook_event_does_not_dispatch_twice(self):
        from app import create_app
        from services.conversation_state import InMemoryConversationStateStore

        app = create_app()
        store = InMemoryConversationStateStore()
        with patch("config.CONVERSATION_FLOW_ROUTER_ENABLED", True), \
             patch("services.conversation_state.get_conversation_state_store", return_value=store), \
             patch("routes.webhook.handler._dispatch_intent", return_value=({"fulfillmentText": "ok"}, 200)) as dispatch:
            client = app.test_client()
            client.post("/webhook", json=_payload("รายงานอาการ", "ReportSymptoms", "evt-1"))
            duplicate = client.post("/webhook", json=_payload("รายงานอาการ", "ReportSymptoms", "evt-1"))

        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(dispatch.call_count, 1)
