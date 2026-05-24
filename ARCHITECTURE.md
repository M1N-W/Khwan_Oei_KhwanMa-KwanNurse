# Architecture

## Overview
KwanNurse-Bot is a Python 3.12 Flask application combining a LINE Messaging API chatbot for post-surgical patient follow-up with a Jinja2/HTMX web dashboard for nurses.

## System Diagram

```
                     LINE Messaging API
                            │
                            ▼
                  ┌────────────────────┐
   Patient ─────► │   /webhook (POST)  │ ──► Dialogflow intents
                  └─────────┬──────────┘     (12 intents)
                            │
                            ▼
                  ┌────────────────────┐
                  │  services/ layer   │ ──► risk_assessment, reminder,
                  │  (orchestration)   │     teleconsult, knowledge,
                  └─────────┬──────────┘     llm, early_warning, ...
                            │
                            ▼
                  ┌────────────────────┐
                  │  database/ layer   │ ──► Google Sheets
                  └────────────────────┘     (gspread + worksheet cache)

                  ┌────────────────────┐
   Nurse ───────► │  /dashboard/*      │ ──► Flask blueprint
   (browser)      │  (Jinja + HTMX)    │     (auth + cache-aware reads
                  └────────────────────┘      + write actions)
```

## Key Components

### HTTP Layer (`routes/`)
- `webhook.py` — LINE/Dialogflow webhook handling 12 intents
- `dashboard/` — Nurse dashboard blueprint
  - `auth_views.py` — Login/logout with bcrypt + CSRF
  - `views.py` — Pages, actions, HTMX partials
  - `templates/` — Jinja2 + Tailwind + HTMX

### Business Logic (`services/`)
- `auth.py` — bcrypt verify, CSRF, rate limit, password policy
- `risk_assessment.py` — Symptom + personal risk scoring
- `reminder.py` — Follow-up scheduling + send
- `scheduler.py` — APScheduler bootstrap (single-owner process)
- `teleconsult.py` — Queue + session orchestration
- `notification.py` — LINE push with retry budget
- `knowledge.py` — Static health guides
- `llm_service.py` — Gemini integration with fallback rule-based

### Data Layer (`database/`)
- Google Sheets via `gspread`
- Worksheet cache for performance

### Infrastructure
- **App factory:** `app.py`
- **Config:** `config.py` — Env config, constants, runtime validation
- **Cache:** Process-local TTL cache
- **Scheduler:** APScheduler with in-memory `MemoryJobStore`
- **Session:** Flask cookie sessions

## Deployment Model
VPS/Docker deployment (not serverless) to preserve resident scheduler and process-local cache. See `DEPLOY_RUNBOOK.md` and `Dockerfile`.

## Resilience Patterns
- TTL cache for hot reads
- Batch updates where possible
- Circuit breaker for LLM calls
- Single-owner scheduler to prevent duplicate jobs
- Runtime config validation on startup

## Constraints
- Python 3.12+
- Must preserve single long-lived owner process for scheduler
- Process-local cache assumes single-instance or sticky sessions
