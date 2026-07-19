"""Routing precedence tests for session-isolated features."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NOW = datetime(2026, 7, 19, 13, 0, tzinfo=timezone.utc)
SESSION = "projects/p/agent/sessions/U1"


class ConversationRouterTests(unittest.TestCase):
    def test_symptom_number_wins_over_misclassified_teleconsult_intent(self):
        from services.conversation_router import resolve_route
        from services.conversation_state import InMemoryConversationStateStore, start_state

        store = InMemoryConversationStateStore(now=lambda: NOW)
        store.start(start_state("U1", "line", "reportsymptoms", now=NOW))
        decision = resolve_route(
            user_id="U1", channel_id="line", query_text="3",
            dialogflow_intent="AfterHoursChoice", dialogflow_params={},
            session_name=SESSION, webhook_event_id="evt-1", store=store, now=NOW,
        )

        self.assertEqual(decision.intent, "ReportSymptoms")
        self.assertEqual(decision.params, {"pain_score": "3"})

    def test_top_level_command_replaces_incomplete_flow(self):
        from services.conversation_router import resolve_route
        from services.conversation_state import InMemoryConversationStateStore, start_state

        store = InMemoryConversationStateStore(now=lambda: NOW)
        store.start(start_state("U1", "line", "appointment", now=NOW))
        decision = resolve_route(
            user_id="U1", channel_id="line", query_text="รายงานอาการ",
            dialogflow_intent="RequestAppointment", dialogflow_params={},
            session_name=SESSION, webhook_event_id="evt-2", store=store, now=NOW,
        )

        self.assertEqual(decision.intent, "ReportSymptoms")
        self.assertEqual(decision.state.flow_id, "reportsymptoms")

    def test_duplicate_event_is_not_dispatched(self):
        from services.conversation_router import resolve_route
        from services.conversation_state import InMemoryConversationStateStore

        store = InMemoryConversationStateStore(now=lambda: NOW)
        store.claim_event("evt-3", 60)
        decision = resolve_route(
            user_id="U1", channel_id="line", query_text="รายงานอาการ",
            dialogflow_intent="ReportSymptoms", dialogflow_params={},
            session_name=SESSION, webhook_event_id="evt-3", store=store, now=NOW,
        )

        self.assertTrue(decision.duplicate)
