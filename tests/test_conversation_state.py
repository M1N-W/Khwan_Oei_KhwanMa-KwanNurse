"""Contract tests for isolated conversational flow state."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NOW = datetime(2026, 7, 19, 13, 0, tzinfo=timezone.utc)


class ConversationStateContractTests(unittest.TestCase):
    def test_symptom_pain_accepts_only_one_to_five(self):
        from services.conversation_state import apply_input, start_state

        state = start_state("U1", "line", "reportsymptoms", now=NOW)
        accepted = apply_input(state, "3", now=NOW)
        rejected = apply_input(state, "6", now=NOW)

        self.assertEqual(accepted.state.slots, {"pain_score": "3"})
        self.assertEqual(accepted.state.step_id, "wound_status")
        self.assertEqual(rejected.state.step_id, "pain_score")
        self.assertIsNotNone(rejected.validation_message)

    def test_risk_negative_answer_maps_only_to_disease_slot(self):
        from services.conversation_state import ConversationState, apply_input

        state = ConversationState(
            user_id="U1", channel_id="line", flow_id="assessrisk",
            flow_instance_id="flow-1", step_id="disease",
            slots={"age": "16", "weight": "60", "height": "170"},
            version=3, expires_at=NOW + timedelta(minutes=15),
        )
        transition = apply_input(state, "ไม่มี", now=NOW)

        self.assertEqual(transition.state.slots["disease"], "ไม่มีโรคประจำตัว")
        self.assertIsNone(transition.state.step_id)

    def test_appointment_day_rejects_out_of_range_number(self):
        from services.conversation_state import apply_input, start_state

        state = start_state("U1", "line", "appointment", now=NOW)
        transition = apply_input(state, "32", now=NOW)

        self.assertEqual(transition.state.step_id, "apt_day")
        self.assertIsNotNone(transition.validation_message)


class ConversationStateStoreTests(unittest.TestCase):
    def test_compare_and_set_rejects_stale_version(self):
        from services.conversation_state import (
            InMemoryConversationStateStore, apply_input, start_state,
        )

        store = InMemoryConversationStateStore(now=lambda: NOW)
        current = store.start(start_state("U1", "line", "reportsymptoms", now=NOW))
        advanced = apply_input(current, "3", now=NOW).state

        self.assertTrue(store.compare_and_set(current, advanced))
        self.assertFalse(store.compare_and_set(current, advanced))

    def test_event_claim_is_idempotent(self):
        from services.conversation_state import InMemoryConversationStateStore

        store = InMemoryConversationStateStore(now=lambda: NOW)
        self.assertTrue(store.claim_event("evt-1", 60))
        self.assertFalse(store.claim_event("evt-1", 60))

    def test_state_key_is_scoped_to_user_and_channel(self):
        from services.conversation_state import state_key

        self.assertNotEqual(state_key("line", "U1"), state_key("line", "U2"))
        self.assertNotEqual(state_key("line", "U1"), state_key("dialogflow", "U1"))
