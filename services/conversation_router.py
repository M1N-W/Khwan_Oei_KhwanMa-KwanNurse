"""Deterministic router that prevents conversation flows from overlapping."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from services.conversation_state import (
    ConversationState, ConversationStateStore, apply_input, start_state,
)

TOP_LEVEL_COMMANDS = {
    "รายงานอาการ": ("reportsymptoms", "ReportSymptoms"),
    "แจ้งอาการ": ("reportsymptoms", "ReportSymptoms"),
    "ประเมินความเสี่ยง": ("assessrisk", "AssessRisk"),
    "ประเมินความเสี่ยงส่วนบุคคล": ("assessrisk", "AssessRisk"),
    "นัดหมายพยาบาล": ("appointment", "RequestAppointment"),
    "นัดหมาย": ("appointment", "RequestAppointment"),
    "ปรึกษาพยาบาล": ("teleconsult", "ContactNurse"),
    "ติดต่อพยาบาล": ("teleconsult", "ContactNurse"),
    "คุยกับพยาบาล": ("teleconsult", "ContactNurse"),
    "ลงทะเบียน": ("registration", "PatientIdentity"),
}

FLOW_INTENTS = {
    "reportsymptoms": "ReportSymptoms",
    "assessrisk": "AssessRisk",
    "appointment": "RequestAppointment",
    "teleconsult": "AfterHoursChoice",
    "registration": "PatientIdentity",
}
FLOW_CONTEXTS = {
    "reportsymptoms": "reportsymptoms_dialog_context",
    "assessrisk": "assessrisk_dialog_context",
    "appointment": "requestappointment_dialog_context",
    "teleconsult": "teleconsult_category_context",
    "registration": "registering",
}
_CANCELS = {"ยกเลิก", "ยกเลิกคำขอ", "ยกเลิกปรึกษา", "ยกเลิกการลงทะเบียน", "ยกเลิกนัด", "ยกเลิกนัดหมาย", "ออก", "ออกจากขั้นตอน", "exit", "cancel"}


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    params: dict[str, str]
    query_text: str
    state: ConversationState | None
    response_text: str | None
    context_operations: tuple[dict[str, object], ...]
    duplicate: bool = False


def normalize(text: str) -> str:
    return "".join(str(text or "").casefold().split())


def _context_operations(session_name: str | None, state: ConversationState | None) -> tuple[dict[str, object], ...]:
    if not session_name:
        return ()
    active = FLOW_CONTEXTS.get(state.flow_id) if state and state.step_id else None
    operations = []
    for name in FLOW_CONTEXTS.values():
        if name != active:
            operations.append({"name": f"{session_name}/contexts/{name}", "lifespanCount": 0})
    if active:
        operations.append({"name": f"{session_name}/contexts/{active}", "lifespanCount": 5})
    return tuple(operations)


def resolve_route(
    *, user_id: str, channel_id: str, query_text: str, dialogflow_intent: str,
    dialogflow_params: Mapping[str, object], session_name: str | None,
    webhook_event_id: str | None, store: ConversationStateStore,
    now: datetime | None = None,
) -> RouteDecision:
    from config import CONVERSATION_EVENT_TTL_SECONDS, CONVERSATION_STATE_TTL_SECONDS

    if webhook_event_id and not store.claim_event(webhook_event_id, CONVERSATION_EVENT_TTL_SECONDS):
        return RouteDecision("", {}, query_text, None, None, (), duplicate=True)
    text = str(query_text or "").strip()
    command = TOP_LEVEL_COMMANDS.get(normalize(text))
    if normalize(text) in _CANCELS:
        current = store.get(user_id, channel_id)
        if current:
            store.clear(current)
        return RouteDecision("CancelConsultation", {}, text, None, None, _context_operations(session_name, None))
    if command:
        flow_id, intent = command
        state = start_state(user_id, channel_id, flow_id, now=now, ttl_seconds=CONVERSATION_STATE_TTL_SECONDS)
        store.start(state)
        return RouteDecision(intent, {}, text, state, None, _context_operations(session_name, state))
    current = store.get(user_id, channel_id)
    if current:
        transition = apply_input(current, text, now=now)
        if transition.validation_message:
            return RouteDecision(
                FLOW_INTENTS[current.flow_id], dict(current.slots), text, current,
                transition.validation_message, _context_operations(session_name, current),
            )
        if transition.state.step_id is None:
            if not store.clear(current):
                return RouteDecision("", {}, text, current, "กรุณาลองใหม่อีกครั้งค่ะ", (), duplicate=True)
            return RouteDecision(
                FLOW_INTENTS[current.flow_id], dict(transition.state.slots), text, None,
                None, _context_operations(session_name, None),
            )
        if not store.compare_and_set(current, transition.state):
            return RouteDecision("", {}, text, current, "กรุณาลองใหม่อีกครั้งค่ะ", (), duplicate=True)
        return RouteDecision(
            FLOW_INTENTS[current.flow_id], dict(transition.state.slots), text,
            transition.state, None, _context_operations(session_name, transition.state),
        )
    return RouteDecision(
        dialogflow_intent, {str(key): str(value) for key, value in dialogflow_params.items()},
        text, None, None, (),
    )
