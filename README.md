# 🏥 KwanNurse-Bot

> ระบบพยาบาลทางไกล (Tele-Nursing) สำหรับติดตามผู้ป่วยหลังผ่าตัด — รวม LINE chatbot
> สำหรับผู้ป่วย และ web dashboard สำหรับพยาบาล ในแอปเดียว.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0-000?logo=flask)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-Internal-lightgrey.svg)](#license)

---

## ✨ Highlights

- **6 LINE features** สำหรับผู้ป่วย: ประเมินอาการ, ประเมินความเสี่ยงส่วนบุคคล, นัดหมาย, ความรู้สุขภาพ, ติดตามอัตโนมัติ, ปรึกษาพยาบาล
- **Nurse Dashboard** ผ่าน `/dashboard/*` — หน้าหลัก, คิวปรึกษา, แจ้งเตือน, timeline ผู้ป่วย, รับ/ปิดเคส, ซ่อน alert
- **Auth ระดับ production** — bcrypt + CSRF + rate limit + idle timeout + password policy
- **Resilience patterns** — TTL cache, batch updates, circuit breaker (LLM), single-owner scheduler, runtime config validation
- **LLM แบบ optional** — Gemini integration พร้อม fallback rule-based และ daily budget guard

---

## 🏗 Architecture

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

ดูสถาปัตยกรรมและการ deploy ฉบับเต็มได้ใน [`DEPLOY_RUNBOOK.md`](./DEPLOY_RUNBOOK.md)
และ [`DASHBOARD_SETUP.md`](./DASHBOARD_SETUP.md).

---

## 📁 Project Structure

```
kwannurse-linebot/
├── app.py                    # Flask app factory + scheduler ownership
├── config.py                 # Env config, constants, runtime validation
├── requirements.txt          # Dependencies
├── README.md                 # This file
├── DASHBOARD_SETUP.md        # Nurse dashboard setup guide
├── DEPLOY_RUNBOOK.md         # Deployment + operations runbook
├── PRODUCT_VISION.md         # Product roadmap & gap analysis
│
├── routes/                   # HTTP layer
│   ├── webhook.py            # LINE/Dialogflow webhook (12 intents)
│   └── dashboard/            # Nurse dashboard blueprint
│       ├── auth_views.py     # Login/logout
│       ├── views.py          # Pages + actions + HTMX partials
│       └── templates/        # Jinja2 + Tailwind + HTMX
│
├── services/                 # Business logic
│   ├── auth.py               # bcrypt verify, CSRF, rate limit, password policy
│   ├── risk_assessment.py    # Symptom + personal risk scoring
│   ├── reminder.py           # Follow-up scheduling + send
│   ├── scheduler.py          # APScheduler bootstrap (single-owner)
│   ├── teleconsult.py        # Queue + session orchestration
│   ├── notification.py       # LINE push (with retry budget)
│   ├── knowledge.py          # Static health guides
│   ├── education.py          # Topic-based recommendations
│   ├── nlp.py                # Free-text triage
│   ├── llm.py                # Gemini integration with circuit breaker
│   ├── early_warning.py      # Concern keyword scan + nurse alerts
│   ├── presession.py         # Pre-teleconsult patient context
│   ├── appointment.py        # Appointment booking
│   ├── cache.py              # In-memory TTL cache (singleton)
│   ├── metrics.py            # In-process counters
│   ├── dashboard_readers.py  # Cache-aware reads for dashboard
│   └── dashboard_actions.py  # Write actions + cache invalidate + audit log
│
├── database/                 # Data layer (Google Sheets)
│   ├── sheets.py             # Shared client + worksheet cache
│   ├── reminders.py          # Reminder schedule/sent/response/no-response
│   └── teleconsult.py        # Sessions + queue
│
├── utils/                    # Helpers
│   ├── parsers.py            # Date/time/phone parsing
│   └── pii.py                # PII redaction utilities
│
├── scripts/
│   └── make_nurse_hash.py    # Generate bcrypt hash with policy enforcement
│
├── dialogflow/               # Dialogflow agent export (intents + entities)
│
└── test_*.py                 # Unit + integration tests (13 suites, ~190 tests)
```

---

## 🚀 Quick Start

### 1) Local development

```pwsh
# Clone & install
git clone <repo-url> kwannurse-linebot
cd kwannurse-linebot
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run app (LINE bot only — dashboard disabled if NURSE_DASHBOARD_AUTH unset)
$env:RUN_SCHEDULER = "false"   # disable scheduler in local dev
python app.py
# → http://localhost:5000/healthz
```

### 2) Enable Nurse Dashboard locally

```pwsh
# Generate a bcrypt hash for the nurse password
python scripts\make_nurse_hash.py nurse_kwan 'YourStrongPass123'
# → nurse_kwan:$2b$12$...

# Set required env vars
$env:FLASK_SECRET_KEY = (python -c "import secrets;print(secrets.token_hex(32))")
$env:NURSE_DASHBOARD_AUTH = "nurse_kwan:$2b$12$..."  # paste from above
$env:DEBUG = "true"   # allow non-HTTPS cookie in local dev

python app.py
# → http://localhost:5000/dashboard/login
```

ดูคู่มือเต็มใน [`DASHBOARD_SETUP.md`](./DASHBOARD_SETUP.md).

### 3) Deploy on Render

```yaml
# render.yaml (or service settings)
buildCommand: pip install -r requirements.txt
startCommand: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 30
```

ตั้ง env vars ตามตารางใน [`DEPLOY_RUNBOOK.md`](./DEPLOY_RUNBOOK.md) — โดยเฉพาะ
`CHANNEL_ACCESS_TOKEN`, `GSPREAD_CREDENTIALS`, `WORKSHEET_LINK`, และ
`NURSE_DASHBOARD_AUTH` + `FLASK_SECRET_KEY` (สำหรับ dashboard).

---

## 🎯 Features

### 👤 Patient-facing (LINE chatbot)

| Feature | Intent | Description |
|---|---|---|
| **Symptom triage** | `ReportSymptoms` | ประเมินอาการ pain/wound/fever/mobility → คำนวณ risk → push alert พยาบาลถ้า high |
| **Personal risk** | `AssessRisk` | คำนวณความเสี่ยงจาก demographics + diseases + BMI |
| **Appointments** | `RequestAppointment` | นัดหมายกับพยาบาล + แจ้งกลุ่ม |
| **Knowledge** | `GetKnowledge`, `RecommendKnowledge` | คู่มือดูแลแผล / กายภาพ / ยา / ป้องกันแทรกซ้อน |
| **Follow-up** | `GetFollowUpSummary` | ติดตามอัตโนมัติตามรอบเวลา + alert ถ้าไม่ตอบ 24 ชม. |
| **Teleconsult** | `ContactNurse`, `AfterHoursChoice`, `CancelConsultation` | คิวปรึกษาพยาบาล (queued / in_progress / after-hours / emergency) |
| **Free-text triage** | `FreeTextSymptom` | วิเคราะห์ข้อความอิสระ + escalate ถ้าเสี่ยงสูง |

### 🩺 Nurse-facing (Web dashboard)

| Page | Path | Function |
|---|---|---|
| Home | `/dashboard/` | สถิติรวม + preview คิว/แจ้งเตือน + bell badge |
| Queue | `/dashboard/queue` | ตารางคิว — รับเคส / ปิดเคส (HTMX auto-refresh) |
| Alerts | `/dashboard/alerts` | รายการแจ้งเตือนกรองตาม days/level — ซ่อนได้ 24 ชม. |
| Patient timeline | `/dashboard/patient/<user_id>` | ประวัติ symptoms + sessions ของผู้ป่วย 1 คน |

ดูรายละเอียด security model + URL map ครบใน [`DASHBOARD_SETUP.md`](./DASHBOARD_SETUP.md).

---

## 🔐 Security Model

| Threat | Control |
|---|---|
| Password brute-force | bcrypt cost 12 + rate limit 5 fails / 5 min / IP |
| Session hijack | Cookie `HttpOnly` + `Secure` (prod) + `SameSite=Lax` |
| CSRF | Token in session, validated on every POST |
| XSS | Jinja auto-escape (no `\|safe` on user input) |
| Open redirect | `_safe_next_url` rejects external URLs |
| Idle session | Auto-logout after 15 min (configurable) |
| Session fixation | `session.clear()` + new CSRF on login |
| bcrypt truncation | Password policy blocks > 72 bytes |
| PHI in logs | `utils/pii.py` redaction utilities |

นโยบายรหัสผ่านบังคับใน `services/auth.py::validate_nurse_password`:
≥ 10 chars, มี upper+lower+digit, ห้าม contain username, ห้าม common password.

---

## 🧪 Testing

```pwsh
# All regression suites (13 suites, ~190 tests)
python run_regression_tests.py

# Single suite
python -m unittest test_dashboard_actions.py -v
```

| Suite | Coverage |
|---|---|
| `test_teleconsult.py` | Queue + session orchestration |
| `test_reminder.py` | Schedule, send, no-response detection |
| `test_llm.py` | Gemini integration + circuit breaker fallback |
| `test_symptom_risk.py` | Risk scoring (pain/wound/fever/mobility) |
| `test_presession.py` | Pre-teleconsult patient context |
| `test_early_warning.py` | Concern keyword scan |
| `test_integration_e2e.py` | End-to-end webhook flows |
| `test_metrics.py` | In-process counters |
| `test_cache.py` | TTL cache eviction & invalidation |
| `test_dashboard_readers.py` | Cache-aware data reads |
| `test_dashboard_actions.py` | Write actions + audit log + dismissal |
| `test_dashboard_auth.py` | Login + CSRF + rate limit + session lifecycle |
| `test_dashboard_polish.py` | Bell endpoint + password policy + script |

---

## 📚 Documentation Map

| Document | When to read |
|---|---|
| [`README.md`](./README.md) | First contact — overview + quick start |
| [`DASHBOARD_SETUP.md`](./DASHBOARD_SETUP.md) | Setting up nurse dashboard (env vars, login flow, security) |
| [`DEPLOY_RUNBOOK.md`](./DEPLOY_RUNBOOK.md) | Deploying to Render + operations runbook |
| [`PRODUCT_VISION.md`](./PRODUCT_VISION.md) | Product roadmap, gaps vs source documents, next sprint |

---

## 🛠 Tech Stack

- **Backend**: Python 3.12, Flask 3.0, Gunicorn
- **Scheduling**: APScheduler (in-memory, single-owner)
- **Storage**: Google Sheets (via `gspread`) — *long-term: migrate to relational store*
- **Auth**: bcrypt 4.1+, Flask session (cookie-based)
- **LLM (optional)**: Google Gemini via `google-generativeai`
- **Frontend (dashboard)**: Jinja2 + Tailwind (CDN) + HTMX (CDN)
- **Hosting**: Render (single-worker Gunicorn)

---

## 🗺 Roadmap

ดู [`PRODUCT_VISION.md`](./PRODUCT_VISION.md) สำหรับ gap analysis ฉบับเต็ม. หัวข้อหลักที่อยู่ในแผน:

- ✅ **Phase 1** — Stabilize core (scheduler, queue, alerts) — *done*
- ✅ **Phase 2** — Performance + reliability (cache, batch updates, circuit breaker) — *done*
- ✅ **Phase 3 Sprint 1** — Nurse dashboard (auth, views, actions, polish) — *done*
- 🟡 **Phase 3 Sprint 2** — Image-based wound analysis, pre-consult summary, personalized education
- 🔜 **Long-term** — Persistent scheduler, async outbox, HIS integration, video consult

---

## 📄 License

Internal/educational use. ไม่รับ contribution จากภายนอก ณ ปัจจุบัน.

---

_Built with ❤️ for แม่ขวัญและทีมพยาบาล._
