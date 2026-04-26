# KwanNurse-Bot — Deploy Runbook (v4.2, Phase 2 complete)

Operational reference for deploying, smoke-testing, and rolling back the
bot. Aimed at a single-node Render deploy with Gunicorn.

---

## 1. Environment variables

### Required (health check will warn if missing)

| Variable | Purpose |
|---|---|
| `CHANNEL_ACCESS_TOKEN` | LINE Messaging API channel access token (nurse push) |
| `NURSE_GROUP_ID` | LINE group ID to page for high-risk / early-warning alerts |
| `GSPREAD_CREDENTIALS` *(or `GOOGLE_CREDS_B64`, or `credentials.json` file)* | Google service-account JSON for Sheets I/O |
| `WORKSHEET_LINK` | Full Google Sheet URL — used for deep-links inside nurse alerts |

Without all four the health check will log a warning; `/metrics` and
`/webhook` still respond but scheduler startup is skipped and early-warning
alerts cannot fire.

### Phase 2 LLM (optional — safe default is off)

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | no | `none` | Set to `gemini` to enable LLM paths |
| `GEMINI_API_KEY` | yes, if `LLM_PROVIDER=gemini` | — | From Google AI Studio |
| `LLM_MODEL` | no | `gemini-2.0-flash` | Override model |
| `LLM_TIMEOUT_SECONDS` | no | `8` | Webhook budget guard |
| `LLM_MAX_OUTPUT_TOKENS` | no | `500` | Cost guard |
| `LLM_DAILY_CALL_LIMIT` | no | `1000` | Soft per-process cap |
| `LLM_CIRCUIT_FAILURE_THRESHOLD` | no | `3` | Consecutive fails open the breaker |
| `LLM_CIRCUIT_COOLDOWN_SECONDS` | no | `60` | Breaker cool-down |

Leave `LLM_PROVIDER=none` for pure rule-based operation. All LLM-using
handlers have rule-based fallbacks and never block the webhook.

### Sprint 2 S2-2 Vision (wound image analysis — optional)

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `LLM_VISION_DAILY_CAP` | no | `200` | Separate daily counter from text LLM |
| `LLM_VISION_TIMEOUT_SECONDS` | no | `12` | Image processing is slower than text |
| `LLM_VISION_MODEL` | no | _(falls back to `LLM_MODEL`)_ | Override only if you want a different vision model |
| `LLM_VISION_MAX_IMAGE_BYTES` | no | `8388608` (8 MB) | Reject oversized uploads before calling Gemini |

Image flow only runs when `LLM_PROVIDER=gemini`. With provider=`none` the
`/line/webhook` endpoint will still 200 OK, save a raw "AI not available"
nurse notice, and reply to the patient with a friendly fallback.

### Scheduler ownership

| Variable | Default | Notes |
|---|---|---|
| `RUN_SCHEDULER` | `true` | Set `false` on all worker replicas except one to prevent duplicate reminders / duplicate early-warning scans |

### Misc

| Variable | Default |
|---|---|
| `PORT` | `5000` |
| `DEBUG` | `false` |

---

## 2. Deploy to Render

1. Connect the GitHub repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 30`
   - Keep `--workers 1` for now so the APScheduler single-owner invariant
     holds without needing a separate worker dyno.
   - If you ever scale workers > 1, set `RUN_SCHEDULER=false` on all but
     one and use a dedicated scheduler process.
4. Paste the env vars from §1.
5. Trigger a deploy and watch the startup log for the banner:

   ```text
   ✅ Scheduler started successfully
   ✅ Scheduled daily no-response check at 10:00
   ✅ Scheduled daily early-warning scan at 11:00
   ✅ Scheduled hourly metrics summary at :00
   ```

### 2.1 LINE Channel webhook URLs (Sprint 2 S2-2)

The bot now exposes **two** webhook endpoints:

| Endpoint | Format | Purpose |
|---|---|---|
| `POST /webhook` | Dialogflow | Text intents (legacy + still primary) |
| `POST /line/webhook` | Raw LINE event | Image messages → wound analysis |

There are two ways to wire LINE → both endpoints:

- **Option A (recommended for v4 → v5 transition)**: Keep Dialogflow as the
  primary LINE webhook for text. Configure a **secondary integration** or
  Channel-level handler that POSTs raw image events to `/line/webhook`.
- **Option B (when you migrate off Dialogflow)**: Set `/line/webhook` as the
  sole LINE webhook URL and forward text events to Dialogflow yourself
  inside the route. (Out of scope for S2-2; tracked in `PRODUCT_VISION.md`.)

For the pilot, Option A is fine — only image flow uses `/line/webhook`.

---

## 3. Smoke tests (post-deploy)

Run in order. Abort and roll back (§5) on any failure.

### 3.1 Health check

```bash
curl -sS https://<host>/ | jq
```

Expect HTTP 200 and the `features` list including `Teleconsult`.

### 3.2 Metrics endpoint

```bash
curl -sS https://<host>/metrics | jq
```

Expect `counters` to be an object (empty immediately after deploy is fine).

### 3.3 Symptom webhook (low risk — must NOT page nurse)

```bash
curl -sS -X POST https://<host>/webhook \
  -H 'Content-Type: application/json' \
  -d '{
    "session": "projects/p/agent/sessions/smoke-low",
    "queryResult": {
      "queryText": "smoke",
      "parameters": {
        "pain_score": 1,
        "wound_status": "ปกติ",
        "fever_check": "ไม่มี",
        "mobility_status": "เดินได้"
      },
      "intent": {"displayName": "ReportSymptoms"}
    }
  }' | jq -r .fulfillmentText
```

Expect a risk-summary reply. Check Render logs: there must be **no**
`Push notification sent` line and the `/metrics` snapshot must show
`line_push.success` unchanged.

### 3.4 Symptom webhook (high risk — MUST page nurse)

Same payload as 3.3 but with `pain_score: 9, wound_status: "แผลมีหนอง",
fever_check: "มีไข้", mobility_status: "ขยับไม่ได้"`. Expect a LINE
message in the nurse group within ~5 seconds. `/metrics` should show
`line_push.success` incremented.

### 3.5 Dialogflow simulator

In Dialogflow Console → Test console, run one utterance per intent:

- `ReportSymptoms`
- `AssessRisk`
- `RequestAppointment`
- `GetKnowledge`
- `FreeTextSymptom` (e.g. `ตอนนี้รู้สึกไม่ค่อยสบาย`)
- `RecommendKnowledge` (e.g. `แนะนำบทความให้หน่อย`)
- `AfterHoursChoice` (e.g. `1`)

Each response should come back within 10 s with no 5xx in Render.

### 3.6 Regression suite (local)

```bash
RUN_SCHEDULER=false python run_regression_tests.py
```

Expect `All regression suites passed.` across 9 suites / 101 tests.

---

## 4. Observability

- **/metrics**: JSON snapshot of in-process counters. Key names:
  - `line_push.success`, `line_push.4xx`, `line_push.gave_up`
  - `early_warning.alert_sent`, `early_warning.dedup_skip`,
    `early_warning.nurse_group_missing`
  - `llm.call_success`, `llm.call_timeout`, `llm.call_network_error`,
    `llm.skip_circuit_open`, `llm.skip_quota`
- **Hourly log summary**: the scheduler emits a single line starting with
  `metrics:` every hour on the :00. Grep Render logs with `metrics:`.
- **Masked IDs**: user IDs in logs are truncated (`xxxx***yyyy`); full IDs
  only appear in `DEBUG=true` mode.

---

## 5. Rollback

1. In Render → Deploys → pick the previous green deploy → **Rollback**.
2. If LLM is misbehaving but code is fine, set `LLM_PROVIDER=none` and
   redeploy — no code change needed; rule-based fallbacks kick in
   immediately.
3. If early-warning alerts are noisy, set `NURSE_GROUP_ID` to an empty
   value temporarily — pushes are suppressed (metric
   `early_warning.nurse_group_missing` will increment) but the webhook
   stays fully functional.

---

## 6. Feature flags cheat sheet

| To disable… | Flip this | Effect |
|---|---|---|
| All LLM paths | `LLM_PROVIDER=none` | `services.nlp`, `services.education`, `services.presession` fall back to rule-based output; no external calls |
| Reminder + early-warning scheduler | `RUN_SCHEDULER=false` | Webhook still serves; no background scans |
| Nurse push entirely | Clear `NURSE_GROUP_ID` | All nurse alerts no-op; counter `line_push.skip_unconfigured` increments |
| Debug logging of full payloads | `DEBUG=false` | Only intent + masked user ID are logged |

---

## 7. Known single points of failure

- **Google Sheets rate limits**: primary datastore. Hot paths batched in
  Phase 1; large scans (early-warning, reminder loader) bounded by
  `limit=` args. Watch for `429` in logs.
- **LINE quota**: push messages share the channel quota. Metric
  `line_push.gave_up` climbing means LINE is 5xx'ing after 3 attempts.
- **APScheduler in-memory store**: jobs are lost on restart.
  `load_pending_reminders()` at startup rehydrates them from Sheets.
