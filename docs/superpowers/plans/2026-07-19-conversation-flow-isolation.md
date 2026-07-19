# Conversation Flow Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement one task at a time. Track every checkbox.

**Goal:** Ensure each reply, especially numeric input, is consumed only by the user's current feature while preserving LINE, Dialogflow ES, Google Sheets, and Gemini integrations.

**Architecture:** Add a deterministic Conversation Flow Controller inside Flask. Its Redis-backed `ConversationState` is the only authority for the active flow, current step, slots, generation, and expiry. Dialogflow supplies an intent candidate and mirrored context only; Google Sheets persists completed clinical/business records; Gemini performs optional bounded enrichment after deterministic validation.

**Tech Stack:** Python 3, Flask 3, Redis, Dialogflow ES, LINE Messaging API, Google Sheets/gspread, Gemini API, unittest, Gunicorn.

## Global Constraints

- No generic numeric route may depend on a Google Sheets row, a teleconsult session, Dialogflow parameter history, or Gemini output.
- Do not invoke Google Sheets or Gemini while deciding which flow owns a reply.
- Production uses Redis only; in-memory state is unit-test/local-development only.
- `CONVERSATION_FLOW_ROUTER_ENABLED` defaults to `false`; enabled non-debug runtime without Redis must fail closed.
- State operations must be O(1) per `channel_id + user_id`; use Redis atomic compare-and-set and TTL.
- Each state has `flow_instance_id` and monotonic `version`; stale or duplicated writes must not advance it.
- A top-level feature command cancels only unfinished conversational slots. It does not cancel persisted teleconsult business sessions.
- Deduplicate LINE events by `webhookEventId` before state mutation.
- Validate every Dialogflow/Gemini-extracted value by the step schema in application code.
- Do not alter production Dialogflow, Sheets, Redis, deployment, or unrelated dirty files without separate authorization.

## File Map

| File | Responsibility |
|---|---|
| `services/conversation_state.py` | State dataclasses, flow contracts, pure validators, Redis/in-memory stores, event idempotency. |
| `services/conversation_router.py` | Route precedence and Dialogflow context mirror operations. |
| `routes/webhook/handler.py` | Extract inbound metadata, call router, dispatch existing handlers. |
| `routes/webhook/handlers/symptoms.py` | Finalize validated symptom/risk/appointment records only. |
| `routes/webhook/handlers/fallback.py` | Consume teleconsult category only when teleconsult flow owns it. |
| `routes/webhook/handlers/registration.py` | Finalize validated registration fields only. |
| `services/dialogflow_bridge.py` | Receive optional mirrored runtime contexts. |
| `config.py`, `requirements.txt` | Flags, Redis configuration, strict runtime validation, Redis client. |
| `tests/test_conversation_state.py` | Unit coverage for contracts, stores, expiry, idempotency. |
| `tests/test_conversation_router.py` | Route precedence and cross-flow isolation coverage. |
| `tests/test_conversation_flow_e2e.py` | Flask-level flow, retry, failure, and restart coverage. |
| `docs/OPERATIONS_CONVERSATION_FLOW.md` | Staging, monitoring, rollback, and production approval runbook. |

## Shared Interfaces

```python
# services/conversation_state.py
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
    step_id: str
    slots: Mapping[str, str]
    version: int
    expires_at: datetime

class ConversationStateStore(Protocol):
    def get(self, user_id: str, channel_id: str) -> ConversationState | None: ...
    def start(self, state: ConversationState) -> ConversationState: ...
    def compare_and_set(self, expected: ConversationState, next_state: ConversationState) -> bool: ...
    def clear(self, expected: ConversationState) -> bool: ...
    def claim_event(self, webhook_event_id: str, ttl_seconds: int) -> bool: ...

# services/conversation_router.py
@dataclass(frozen=True)
class RouteDecision:
    intent: str
    params: dict[str, str]
    query_text: str
    state: ConversationState | None
    response_text: str | None
    context_operations: tuple[dict[str, object], ...]
    duplicate: bool = False

def resolve_route(*, user_id: str, channel_id: str, query_text: str,
                  dialogflow_intent: str, dialogflow_params: Mapping[str, object],
                  session_name: str | None, webhook_event_id: str | None,
                  store: ConversationStateStore) -> RouteDecision: ...
```

## Task 1: Add configuration and test seam

**Files:** Modify `requirements.txt`, `config.py`, `app.py`; create `tests/test_conversation_state.py`.

- [ ] Write failing tests that assert the router is disabled by default and that an enabled, non-debug runtime without `CONVERSATION_STATE_REDIS_URL` returns `conversation_router_ready=False` from `validate_runtime_config()`.
- [ ] Run `python -m unittest tests.test_conversation_state.ConversationRouterConfigTests -v`; expect failure because the settings do not yet exist.
- [ ] Add `redis==5.0.8` and the exact settings below. Extend `validate_runtime_config()` and `create_app()` so startup logs a non-secret readiness result and refuses router activation when Redis is absent.

```python
CONVERSATION_FLOW_ROUTER_ENABLED = os.environ.get(
    "CONVERSATION_FLOW_ROUTER_ENABLED", "false"
).lower() in ("1", "true", "yes")
CONVERSATION_STATE_REDIS_URL = os.environ.get("CONVERSATION_STATE_REDIS_URL", "").strip()
CONVERSATION_STATE_TTL_SECONDS = int(os.environ.get("CONVERSATION_STATE_TTL_SECONDS", "900"))
CONVERSATION_EVENT_TTL_SECONDS = int(os.environ.get("CONVERSATION_EVENT_TTL_SECONDS", "86400"))
```

- [ ] Run `python -m unittest tests.test_conversation_state.ConversationRouterConfigTests -v`; expect PASS.
- [ ] Commit only these files: `git add -- requirements.txt config.py app.py tests/test_conversation_state.py` then `git commit -m "feat: add conversation router configuration"`.

## Task 2: Define state contracts and pure validation

**Files:** Create `services/conversation_state.py`; modify `tests/test_conversation_state.py`.

- [ ] Write failing tests for: symptom pain accepts only `1..5`; risk age accepts `0..120`; weight `1..500`; height `30..260`; risk disease maps `ไม่มี` to `ไม่มีโรคประจำตัว`; appointment day rejects `32`; teleconsult accepts category choices only in its own contract.
- [ ] Run `python -m unittest tests.test_conversation_state.ConversationStateContractTests -v`; expect import failure.
- [ ] Implement immutable states and `apply_input(state, text, now)`. Contracts must declare the precise ordered steps:

```python
FLOW_CONTRACTS = {
    "reportsymptoms": ("pain_score", "wound_status", "fever_check", "mobility_status"),
    "assessrisk": ("age", "weight", "height", "disease"),
    "appointment": ("apt_day", "apt_month", "apt_year", "preferred_time", "reason"),
    "teleconsult": ("issue_category",),
    "registration": ("first_name", "last_name", "hn", "phone", "consent"),
}
```

`apply_input()` must be pure: no Flask, environment, Sheets, Redis, Gemini, or Dialogflow access. It returns the same state/step plus a Thai validation message for invalid input, or a new state with `version + 1` and the next declared step for valid input.
- [ ] Run `python -m unittest tests.test_conversation_state -v` and `python -m py_compile services/conversation_state.py`; expect PASS/exit 0.
- [ ] Commit: `git add -- services/conversation_state.py tests/test_conversation_state.py`; `git commit -m "feat: define conversation flow contracts"`.

## Task 3: Add atomic Redis storage and event idempotency

**Files:** Modify `services/conversation_state.py`, `tests/test_conversation_state.py`.

- [ ] Write failing tests for stale `compare_and_set`, duplicate `claim_event`, per-user/channel key isolation, TTL expiry, and replacement by a new `flow_instance_id`.
- [ ] Run `python -m unittest tests.test_conversation_state.ConversationStateStoreTests -v`; expect failure.
- [ ] Implement `InMemoryConversationStateStore` with `threading.Lock` for tests and `RedisConversationStateStore` for production. Use the following key contract:

```python
def state_key(channel_id: str, user_id: str) -> str:
    return f"kwannurse:conversation:v1:{channel_id}:{user_id}"

def event_key(webhook_event_id: str) -> str:
    return f"kwannurse:webhook-event:v1:{webhook_event_id}"

def claim_event(self, event_id: str, ttl_seconds: int) -> bool:
    return bool(self._client.set(event_key(event_id), "1", nx=True, ex=ttl_seconds))
```

Implement Redis CAS with `WATCH/MULTI/EXEC` or a Lua script that compares both stored `version` and `flow_instance_id`. Never silently fall back to the in-memory store when the flag is enabled outside DEBUG.
- [ ] Run `python -m unittest tests.test_conversation_state -v`; expect PASS.
- [ ] Commit: `git add -- services/conversation_state.py tests/test_conversation_state.py`; `git commit -m "feat: persist conversation state atomically"`.

## Task 4: Build deterministic router and Dialogflow context mirroring

**Files:** Create `services/conversation_router.py`, `tests/test_conversation_router.py`; modify `services/dialogflow_bridge.py`.

- [ ] Write failing tests for precedence: duplicate event; cancel; explicit top-level command; valid current step; invalid current step; then Dialogflow candidate only with no active state. Include the incident case: active `reportsymptoms.pain_score` plus Dialogflow `AfterHoursChoice` and input `3` must return `ReportSymptoms` with `{"pain_score": "3"}`.
- [ ] Run `python -m unittest tests.test_conversation_router -v`; expect failure.
- [ ] Implement the exact precedence below. `start_requested_flow()` creates a new UUID `flow_instance_id`, discards unfinished slots, and builds clear operations for all competing Dialogflow contexts. It must not call `cancel_consultation()`.

```python
TOP_LEVEL_COMMANDS = {
    "รายงานอาการ": ("reportsymptoms", "ReportSymptoms"),
    "แจ้งอาการ": ("reportsymptoms", "ReportSymptoms"),
    "ประเมินความเสี่ยง": ("assessrisk", "AssessRisk"),
    "ประเมินความเสี่ยงส่วนบุคคล": ("assessrisk", "AssessRisk"),
    "นัดหมายพยาบาล": ("appointment", "RequestAppointment"),
    "นัดหมาย": ("appointment", "RequestAppointment"),
    "ปรึกษาพยาบาล": ("teleconsult", "ContactNurse"),
    "ติดต่อพยาบาล": ("teleconsult", "ContactNurse"),
}

def resolve_route(...):
    if webhook_event_id and not store.claim_event(webhook_event_id, CONVERSATION_EVENT_TTL_SECONDS):
        return RouteDecision("", {}, query_text, None, None, (), duplicate=True)
    if is_cancel_command(query_text):
        return cancel_active_flow(...)
    if command := TOP_LEVEL_COMMANDS.get(normalize(query_text)):
        return start_requested_flow(command, ...)
    if state := store.get(user_id, channel_id):
        return consume_current_step(state, query_text, ...)
    return route_dialogflow_candidate(dialogflow_intent, dialogflow_params, query_text, ...)
```

Update `detect_intent(user_id, text, contexts=None)` to add optional `queryParams.contexts`. Contexts are output from the router; they are never read back as authoritative slot values.
- [ ] Run `python -m unittest tests.test_conversation_router tests.test_line_bridge -v`; expect PASS.
- [ ] Commit: `git add -- services/conversation_router.py services/dialogflow_bridge.py tests/test_conversation_router.py`; `git commit -m "feat: route replies by active conversation flow"`.

## Task 5: Integrate router and remove global numeric hijacking

**Files:** Modify `routes/webhook/handler.py`, `tests/test_consultation_regressions.py`; create `tests/test_conversation_flow_e2e.py`.

- [ ] Add Flask regressions from production logs: with active symptom state and a persisted queued teleconsult row, `3`, `4`, and `5` dispatch only `ReportSymptoms`; active risk state plus `ไม่มี` dispatches only `AssessRisk`; appointment day `15` dispatches only `RequestAppointment`.
- [ ] Run `python -m unittest tests.test_consultation_regressions tests.test_conversation_flow_e2e -v`; expect failure on the old branch.
- [ ] At the inbound point where user, text, Dialogflow intent, params, session, and event ID exist, replace per-feature context interception with one call to `resolve_route()`. Return HTTP 200/no duplicate persistence when `decision.duplicate=True`; return the router's validation response without dispatch if it has `response_text`; otherwise dispatch `decision.intent`, `decision.params`, and `decision.query_text`.
- [ ] Delete the branch that makes a bare `1..5` into `AfterHoursChoice` because `_has_active_teleconsult_session(user_id)` is true. Keep that durable lookup only inside business operations if still needed; it may not affect routing.
- [ ] Run `python -m unittest tests.test_consultation_regressions tests.test_webhook_handlers tests.test_hotfix_logsec_and_choice -v` and `python -m unittest discover -s tests -q`; expect PASS.
- [ ] Commit: `git add -- routes/webhook/handler.py tests/test_consultation_regressions.py tests/test_conversation_flow_e2e.py`; `git commit -m "fix: isolate numeric replies from teleconsult state"`.

## Task 6: Migrate ReportSymptoms and AssessRisk

**Files:** Modify `routes/webhook/handlers/symptoms.py`, `tests/test_conversation_flow_e2e.py`, `tests/test_symptom_risk.py`, `tests/test_symptom_reliability.py`.

- [ ] Add end-to-end tests: `รายงานอาการ -> 3 -> แผลแดงซึม -> ไม่มีไข้ -> เดินได้ปกติ` must persist exactly once; `ประเมินความเสี่ยง -> 16 -> 60 -> 170 -> ไม่มี` must call risk calculation with disease `ไม่มีโรคประจำตัว`; symptom `3` must never populate risk age.
- [ ] Run `python -m unittest tests.test_conversation_flow_e2e tests.test_symptom_risk -v`; expect failure while handlers rely on contexts.
- [ ] Refactor `handle_report_symptoms()` and `handle_assess_risk()` to receive complete router-validated `params` only. Preserve clinical risk calculation, notification, and Sheets persistence behavior. Remove `_report_symptoms_context()` and `_assess_risk_context()` as state stores; handlers may return context-clear operations only after completion.
- [ ] Guard incomplete dispatch with a contract exception and return the current flow's retry prompt. Never infer a missing field from another flow or Dialogflow context.
- [ ] Run `python -m unittest tests.test_symptom_risk tests.test_symptom_reliability tests.test_conversation_flow_e2e -v`; expect PASS.
- [ ] Commit: `git add -- routes/webhook/handlers/symptoms.py tests/test_conversation_flow_e2e.py tests/test_symptom_risk.py tests/test_symptom_reliability.py`; `git commit -m "feat: migrate symptom and risk flows to state controller"`.

## Task 7: Migrate appointment, teleconsult, and registration

**Files:** Modify `routes/webhook/handlers/symptoms.py`, `routes/webhook/handlers/fallback.py`, `routes/webhook/handlers/registration.py`, `tests/test_conversation_flow_e2e.py`, `tests/test_patient_registration.py`, `tests/test_teleconsult.py`.

- [ ] Add tests: appointment `15` is `apt_day`, not category 4; teleconsult `4` starts category `appointment` only while teleconsult state is active; start symptom flow during partial registration drops registration slots but does not erase a persisted profile; a durable teleconsult queue row does not own a later appointment or symptom reply.
- [ ] Run `python -m unittest tests.test_conversation_flow_e2e tests.test_patient_registration tests.test_teleconsult -v`; expect failure before migration.
- [ ] Remove `ctx_params` extraction and smart-merge from `handle_request_appointment()`; accept router fields `apt_day`, `apt_month`, `apt_year`, `preferred_time`, and `reason`, while retaining existing date/time validation utilities.
- [ ] Change `handle_after_hours_choice()` to accept only router-validated `params["issue_category"]` in `{emergency, medication, wound, appointment, other}`. Do not parse generic numeric `query_text` in this handler.
- [ ] Move registration step advancement into the registration flow contract; invoke existing profile persistence only after all required fields and consent are valid.
- [ ] Run `python -m unittest tests.test_conversation_flow_e2e tests.test_consultation_regressions tests.test_patient_registration tests.test_teleconsult -v`; expect PASS.
- [ ] Commit: `git add -- routes/webhook/handlers/symptoms.py routes/webhook/handlers/fallback.py routes/webhook/handlers/registration.py tests/test_conversation_flow_e2e.py tests/test_patient_registration.py tests/test_teleconsult.py`; `git commit -m "feat: migrate remaining session flows to state controller"`.

## Task 8: Add observability and controlled failure behavior

**Files:** Modify `services/observability.py`, `routes/webhook/handler.py`, `tests/test_phase4_observability.py`, `tests/test_conversation_flow_e2e.py`; create `docs/OPERATIONS_CONVERSATION_FLOW.md`.

- [ ] Write tests that logs contain `flow_id`, `step_id`, source, and version but never slot values; Redis outage returns a generic retry reply and dispatches no Dialogflow numeric choice; CAS conflict reloads once and retries only when `flow_instance_id` is unchanged.
- [ ] Run `python -m unittest tests.test_phase4_observability tests.test_conversation_flow_e2e -v`; expect failure.
- [ ] Emit these counters without PII: `conversation.route.<flow>.<step>`, `conversation.duplicate_event`, `conversation.validation_rejected`, `conversation.cas_conflict`, `conversation.store_unavailable`, `conversation.cross_flow_blocked`.
- [ ] When Redis is unavailable with the flag enabled, return `ขออภัยค่ะ ระบบสนทนาขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้งค่ะ`; do not dispatch the message to an alternate feature.
- [ ] Document Redis TLS/configuration, key prefixes, TTLs, metrics, alert thresholds, feature flag, health verification, rollback, and incident evidence capture in `docs/OPERATIONS_CONVERSATION_FLOW.md`.
- [ ] Run `python -m unittest tests.test_phase4_observability tests.test_conversation_flow_e2e -v` and `python -m unittest discover -s tests -q`; expect PASS.
- [ ] Commit: `git add -- services/observability.py routes/webhook/handler.py tests/test_phase4_observability.py tests/test_conversation_flow_e2e.py docs/OPERATIONS_CONVERSATION_FLOW.md`; `git commit -m "feat: observe and operate conversation state routing"`.

## Task 9: Staging acceptance and production gate

**Files:** Modify `docs/OPERATIONS_CONVERSATION_FLOW.md`, `docs/superpowers/specs/2026-07-19-conversation-flow-isolation-design.md`.

- [ ] Add exact acceptance sequences and expected prompts:

```text
รายงานอาการ -> 3 -> แผลแดงซึม -> ไม่มีไข้ -> เดินได้ปกติ
ประเมินความเสี่ยง -> 16 -> 60 -> 170 -> ไม่มี
นัดหมายพยาบาล -> 15 -> 9 -> 2569 -> เช้า -> ติดตามอาการ
ปรึกษาพยาบาล -> 4
```

- [ ] For every sequence test duplicate `webhookEventId`, out-of-order event timestamp, stale Dialogflow context, active durable teleconsult row, mid-flow new command, cancellation, Redis restart, Sheets `429`, and Gemini timeout. Expected result: no cross-flow state mutation and no duplicate durable record.
- [ ] Run local gates: `git diff --check`; `python -m unittest discover -s tests -q`; `python -m py_compile app.py config.py routes/webhook/handler.py services/conversation_state.py services/conversation_router.py`. Expected: no output/PASS/exit 0.
- [ ] In staging only, set `CONVERSATION_FLOW_ROUTER_ENABLED=true`, `CONVERSATION_STATE_REDIS_URL=<staging TLS Redis URL>`, `CONVERSATION_STATE_TTL_SECONDS=900`, and `CONVERSATION_EVENT_TTL_SECONDS=86400`. Confirm `conversation_router_ready=true` at startup.
- [ ] Run the acceptance matrix, observe zero cross-flow route incidents for 24 hours, then set `CONVERSATION_FLOW_ROUTER_ENABLED=false` and restart staging to prove rollback. Record build SHA, time, Redis health, results, and metrics in the runbook.
- [ ] Production go criteria: all tests/acceptance pass, no store outage/CAS conflict/cross-flow incident during soak, rollback drill passes, Redis availability owner is assigned, and an explicit user authorization is received. Do not push, merge, or deploy as part of this task.
- [ ] Commit documentation: `git add -- docs/OPERATIONS_CONVERSATION_FLOW.md docs/superpowers/specs/2026-07-19-conversation-flow-isolation-design.md`; `git commit -m "docs: add conversation flow rollout evidence"`.

## Self-Review

- The plan implements the spec's single source of conversational truth in Tasks 2-5.
- It keeps Dialogflow as NLU/context compatibility, Sheets as durable persistence, and Gemini as validated optional enrichment.
- It directly covers symptom numeric hijack, risk `ไม่มี`, appointment day/month, teleconsult categories, registration, stale contexts, duplicate LINE events, out-of-order delivery, multi-worker/restart safety, observability, rollback, and staging gates.
- Shared type and function names are defined before any task consumes them.
- Each task has exact file paths, a failing test, a verification command, expected result, and a narrow commit.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-19-conversation-flow-isolation.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh worker per task and review between narrow commits.
2. **Inline Execution** — execute the tasks here in batches with checkpoints.
