"""Atomic, server-side ownership for interactive LINE conversation flows."""
from __future__ import annotations

import json
import hashlib
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal, Mapping, Protocol

FlowId = Literal["reportsymptoms", "assessrisk", "appointment", "teleconsult", "registration"]


@dataclass(frozen=True)
class InputRule:
    kind: Literal["choice", "integer", "decimal", "text", "date", "time"]
    slot: str
    choices: frozenset[str] = frozenset()
    minimum: Decimal | None = None
    maximum: Decimal | None = None


@dataclass(frozen=True)
class ConversationState:
    user_id: str
    channel_id: str
    flow_id: FlowId
    flow_instance_id: str
    step_id: str | None
    slots: Mapping[str, str]
    version: int
    expires_at: datetime


@dataclass(frozen=True)
class StateTransition:
    state: ConversationState
    consumed: bool
    validation_message: str | None = None


class ConversationStateStore(Protocol):
    def get(self, user_id: str, channel_id: str) -> ConversationState | None: ...
    def start(self, state: ConversationState) -> ConversationState: ...
    def compare_and_set(self, expected: ConversationState, next_state: ConversationState) -> bool: ...
    def clear(self, expected: ConversationState) -> bool: ...
    def claim_event(self, webhook_event_id: str, ttl_seconds: int) -> bool: ...


FLOW_RULES: dict[FlowId, tuple[InputRule, ...]] = {
    "reportsymptoms": (
        InputRule("choice", "pain_score", frozenset({"1", "2", "3", "4", "5"})),
        InputRule("choice", "wound_status", frozenset({"แผลแห้งดี", "แผลแดงซึม", "แผลบวมหนอง"})),
        InputRule("choice", "fever_check", frozenset({"ไม่มีไข้", "มีไข้"})),
        InputRule("choice", "mobility_status", frozenset({"เดินได้ปกติ", "ต้องพยุง", "เดินไม่ได้"})),
    ),
    "assessrisk": (
        InputRule("integer", "age", minimum=Decimal("0"), maximum=Decimal("120")),
        InputRule("decimal", "weight", minimum=Decimal("1"), maximum=Decimal("500")),
        InputRule("decimal", "height", minimum=Decimal("30"), maximum=Decimal("260")),
        InputRule("text", "disease"),
    ),
    "appointment": (
        InputRule("integer", "apt_day", minimum=Decimal("1"), maximum=Decimal("31")),
        InputRule("integer", "apt_month", minimum=Decimal("1"), maximum=Decimal("12")),
        InputRule("integer", "apt_year", minimum=Decimal("2020"), maximum=Decimal("2600")),
        InputRule("time", "preferred_time"),
        InputRule("text", "reason"),
    ),
    "teleconsult": (
        InputRule("choice", "issue_category", frozenset({"1", "2", "3", "4", "5"})),
    ),
    "registration": (
        InputRule("text", "first_name"),
        InputRule("text", "last_name"),
        InputRule("text", "hn"),
        InputRule("text", "citizen_id"),
        InputRule("text", "phone"),
        InputRule("choice", "consent", frozenset({"ยอมรับ", "ไม่ยอมรับ"})),
    ),
}

_NEGATIVE_DISEASES = {"ไม่มี", "ไม่มีโรค", "ไม่มีโรคประจำตัว", "ไม่เป็นโรคอะไร", "none", "no", "no disease"}
_TELECONSULT_CATEGORIES = {"1": "emergency", "2": "medication", "3": "wound", "4": "appointment", "5": "other"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def state_key(channel_id: str, user_id: str) -> str:
    digest = hashlib.sha256(f"{channel_id}:{user_id}".encode("utf-8")).hexdigest()
    return f"kwannurse:conversation:v1:{digest}"


def event_key(webhook_event_id: str) -> str:
    return f"kwannurse:webhook-event:v1:{webhook_event_id}"


def start_state(
    user_id: str, channel_id: str, flow_id: FlowId, *, now: datetime | None = None,
    ttl_seconds: int = 900,
) -> ConversationState:
    now = now or _utc_now()
    return ConversationState(
        user_id=user_id, channel_id=channel_id, flow_id=flow_id,
        flow_instance_id=uuid.uuid4().hex, step_id=FLOW_RULES[flow_id][0].slot,
        slots={}, version=1, expires_at=now + timedelta(seconds=ttl_seconds),
    )


def _rule_for(state: ConversationState) -> InputRule | None:
    for rule in FLOW_RULES[state.flow_id]:
        if rule.slot == state.step_id:
            return rule
    return None


def _validation_message(rule: InputRule) -> str:
    labels = {
        "pain_score": "กรุณาเลือกระดับความปวด 1-5 ค่ะ",
        "apt_day": "กรุณาระบุวันที่ 1-31 ค่ะ",
        "apt_month": "กรุณาระบุเดือน 1-12 ค่ะ",
        "age": "กรุณาระบุอายุ 0-120 ปีค่ะ",
        "weight": "กรุณาระบุน้ำหนักให้ถูกต้องค่ะ",
        "height": "กรุณาระบุส่วนสูงให้ถูกต้องค่ะ",
        "issue_category": "กรุณาเลือกหมายเลขจากเมนูนี้ค่ะ",
    }
    return labels.get(rule.slot, "ข้อมูลไม่ถูกต้อง กรุณาลองใหม่อีกครั้งค่ะ")


def _normalize_value(rule: InputRule, text: str) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None
    if rule.kind == "choice":
        return value if value in rule.choices else None
    if rule.kind in {"integer", "decimal"}:
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return None
        if rule.kind == "integer" and parsed != parsed.to_integral_value():
            return None
        if rule.minimum is not None and parsed < rule.minimum:
            return None
        if rule.maximum is not None and parsed > rule.maximum:
            return None
        return str(int(parsed)) if rule.kind == "integer" else format(parsed.normalize(), "f")
    if rule.kind == "time":
        if value in {"เช้า", "สาย", "เที่ยง", "บ่าย", "เย็น", "กลางคืน"}:
            return value
        import re
        return value if re.fullmatch(r"(?:[01]?\d|2[0-3])[:.]\d{2}", value) else None
    return value


def apply_input(state: ConversationState, text: str, *, now: datetime | None = None) -> StateTransition:
    now = now or _utc_now()
    if state.expires_at <= now:
        return StateTransition(state, consumed=False, validation_message="รายการหมดอายุแล้ว กรุณาเริ่มใหม่อีกครั้งค่ะ")
    rule = _rule_for(state)
    if rule is None:
        return StateTransition(state, consumed=False)
    value = _normalize_value(rule, text)
    if value is None:
        return StateTransition(state, consumed=True, validation_message=_validation_message(rule))
    if state.flow_id == "assessrisk" and rule.slot == "disease" and value.casefold() in _NEGATIVE_DISEASES:
        value = "ไม่มีโรคประจำตัว"
    if state.flow_id == "teleconsult" and rule.slot == "issue_category":
        value = _TELECONSULT_CATEGORIES[value]
    next_slot = None
    slots = dict(state.slots)
    slots[rule.slot] = value
    rules = FLOW_RULES[state.flow_id]
    for index, candidate in enumerate(rules):
        if candidate.slot == rule.slot and index + 1 < len(rules):
            next_slot = rules[index + 1].slot
            break
    return StateTransition(
        ConversationState(
            user_id=state.user_id, channel_id=state.channel_id, flow_id=state.flow_id,
            flow_instance_id=state.flow_instance_id, step_id=next_slot, slots=slots,
            version=state.version + 1, expires_at=state.expires_at,
        ),
        consumed=True,
    )


def _serialize(state: ConversationState) -> str:
    payload = asdict(state)
    payload["expires_at"] = state.expires_at.isoformat()
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _deserialize(value: str | bytes | None) -> ConversationState | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    raw = json.loads(value)
    return ConversationState(
        user_id=raw["user_id"], channel_id=raw["channel_id"], flow_id=raw["flow_id"],
        flow_instance_id=raw["flow_instance_id"], step_id=raw["step_id"],
        slots=raw["slots"], version=int(raw["version"]),
        expires_at=datetime.fromisoformat(raw["expires_at"]),
    )


class InMemoryConversationStateStore:
    def __init__(self, *, now: Callable[[], datetime] = _utc_now):
        self._now = now
        self._states: dict[str, ConversationState] = {}
        self._events: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str, channel_id: str) -> ConversationState | None:
        key = state_key(channel_id, user_id)
        with self._lock:
            state = self._states.get(key)
            if state and state.expires_at <= self._now():
                self._states.pop(key, None)
                return None
            return state

    def start(self, state: ConversationState) -> ConversationState:
        with self._lock:
            self._states[state_key(state.channel_id, state.user_id)] = state
        return state

    def compare_and_set(self, expected: ConversationState, next_state: ConversationState) -> bool:
        key = state_key(expected.channel_id, expected.user_id)
        with self._lock:
            current = self._states.get(key)
            if current != expected:
                return False
            self._states[key] = next_state
            return True

    def clear(self, expected: ConversationState) -> bool:
        key = state_key(expected.channel_id, expected.user_id)
        with self._lock:
            if self._states.get(key) != expected:
                return False
            self._states.pop(key, None)
            return True

    def claim_event(self, webhook_event_id: str, ttl_seconds: int) -> bool:
        now = self._now()
        with self._lock:
            self._events = {key: expires for key, expires in self._events.items() if expires > now}
            if webhook_event_id in self._events:
                return False
            self._events[webhook_event_id] = now + timedelta(seconds=ttl_seconds)
            return True


class RedisConversationStateStore:
    """Redis store with optimistic state replacement; imported lazily for tests."""
    def __init__(self, redis_url: str):
        import redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def get(self, user_id: str, channel_id: str) -> ConversationState | None:
        return _deserialize(self._client.get(state_key(channel_id, user_id)))

    def start(self, state: ConversationState) -> ConversationState:
        ttl = max(1, int((state.expires_at - _utc_now()).total_seconds()))
        self._client.set(state_key(state.channel_id, state.user_id), _serialize(state), ex=ttl)
        return state

    def compare_and_set(self, expected: ConversationState, next_state: ConversationState) -> bool:
        key = state_key(expected.channel_id, expected.user_id)
        ttl = max(1, int((next_state.expires_at - _utc_now()).total_seconds()))
        with self._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    current = _deserialize(pipe.get(key))
                    if current != expected:
                        pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.set(key, _serialize(next_state), ex=ttl)
                    pipe.execute()
                    return True
                except Exception as exc:
                    if exc.__class__.__name__ == "WatchError":
                        return False
                    raise

    def clear(self, expected: ConversationState) -> bool:
        key = state_key(expected.channel_id, expected.user_id)
        with self._client.pipeline() as pipe:
            pipe.watch(key)
            if _deserialize(pipe.get(key)) != expected:
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.delete(key)
            pipe.execute()
            return True

    def claim_event(self, webhook_event_id: str, ttl_seconds: int) -> bool:
        return bool(self._client.set(event_key(webhook_event_id), "1", nx=True, ex=ttl_seconds))


_local_store = InMemoryConversationStateStore()


def get_conversation_state_store() -> ConversationStateStore:
    from config import (
        CONVERSATION_FLOW_ROUTER_ENABLED, CONVERSATION_STATE_REDIS_URL, DEBUG,
    )
    if CONVERSATION_FLOW_ROUTER_ENABLED and CONVERSATION_STATE_REDIS_URL:
        return RedisConversationStateStore(CONVERSATION_STATE_REDIS_URL)
    if CONVERSATION_FLOW_ROUTER_ENABLED and not DEBUG:
        raise RuntimeError("conversation router requires Redis outside DEBUG")
    return _local_store
