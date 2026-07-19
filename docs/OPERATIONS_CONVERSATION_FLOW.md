# Conversation Flow Controller Operations

## Purpose

The controller isolates interactive feature state from Dialogflow contexts, Google Sheets business records, and Gemini output. It prevents a teleconsult queue record from interpreting a symptom, risk, appointment, or registration reply.

## Required production configuration

```text
CONVERSATION_FLOW_ROUTER_ENABLED=true
CONVERSATION_STATE_REDIS_URL=rediss://<user>:<password>@<host>:<port>/0
CONVERSATION_STATE_TTL_SECONDS=900
CONVERSATION_EVENT_TTL_SECONDS=86400
```

`CONVERSATION_FLOW_ROUTER_ENABLED` defaults to `false`. Never set it to `true` in a non-debug environment without a TLS Redis endpoint. The application fails closed instead of using process-local state, because separate Gunicorn workers would otherwise disagree about the active feature.

## Key contracts

| Key | TTL | Purpose |
|---|---:|---|
| `kwannurse:conversation:v1:<sha256(channel:user)>` | 900 seconds | Current flow, step, slots, generation, version. |
| `kwannurse:webhook-event:v1:<event-id>` | 86400 seconds | LINE webhook de-duplication. |

Redis must use TLS, authentication, encrypted backups, and a retention policy appropriate for transient health conversation metadata. Do not store message text or clinical values in logs.

## Metrics and alerts

- `conversation.route.<flow>.<step>`: normal state-owned routing.
- `conversation.duplicate_event`: duplicate LINE delivery prevented.
- `conversation.validation_rejected`: active flow rejected invalid input.
- `conversation.store_unavailable`: controller failed closed; alert immediately.

Alert if `conversation.store_unavailable > 0` in five minutes, or if any unexpected `AfterHoursChoice` route is observed while the active flow is not `teleconsult`.

## Staging acceptance matrix

Run each sequence through real LINE staging and confirm the next prompt and saved record are correct:

```text
รายงานอาการ -> 3 -> แผลแดงซึม -> ไม่มีไข้ -> เดินได้ปกติ
ประเมินความเสี่ยง -> 16 -> 60 -> 170 -> ไม่มี
นัดหมายพยาบาล -> 15 -> 9 -> 2569 -> เช้า -> ติดตามอาการ
ปรึกษาพยาบาล -> 4
```

For every sequence also verify duplicate `webhookEventId`, stale Dialogflow contexts, a queued teleconsult record, a new top-level command mid-flow, cancellation, Redis restart, Sheets `429`, and Gemini timeout. No case may create a cross-flow transition or duplicate durable record.

## Rollout and rollback

1. Deploy with the flag unset and confirm the hotfix regression tests are green.
2. Configure staging Redis and enable the flag only in staging.
3. Soak for 24 hours with zero store outages and zero cross-flow incidents.
4. Enable the production flag only after a documented approval and staging evidence.
5. To roll back, set `CONVERSATION_FLOW_ROUTER_ENABLED=false` and restart the service. This stops new controller state decisions without deleting Google Sheets records or teleconsult sessions.

Record build SHA, timestamp, Redis health, acceptance result, and rollback result for every rollout.
