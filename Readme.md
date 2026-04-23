# 🏥 KwanNurse-Bot v3.0 (Refactored)

## 📁 Project Structure

```
kwannurse-bot/
├── app.py                      # Main application entry point
├── config.py                   # Configuration management
├── requirements.txt            # Python dependencies
├── .gitignore                 # Git ignore rules
│
├── utils/                      # Utility functions
│   ├── __init__.py
│   └── parsers.py             # Date/time/phone parsing
│
├── database/                   # Data layer
│   ├── __init__.py
│   └── sheets.py              # Google Sheets operations
│
├── services/                   # Business logic
│   ├── __init__.py
│   ├── notification.py        # LINE notifications
│   ├── risk_assessment.py     # Risk calculations
│   └── appointment.py         # Appointment management
│
└── routes/                     # API endpoints
    ├── __init__.py
    └── webhook.py             # Dialogflow webhook handlers
```

## 🎯 Features

### Core Features (Production Ready)

1. **ReportSymptoms** - AI-powered symptom risk assessment
2. **AssessRisk** - Personal health risk stratification
3. **RequestAppointment** - Appointment booking and management

## 🚀 Quick Start

### 1. Installation

```bash
# Clone repository
git clone <your-repo-url>
cd kwannurse-bot

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

Set environment variables:

```bash
# Required
export GSPREAD_CREDENTIALS='{"type":"service_account",...}'
export CHANNEL_ACCESS_TOKEN='your_line_token'
export NURSE_GROUP_ID='your_line_group_id'

# Optional
export WORKSHEET_LINK='https://docs.google.com/spreadsheets/d/...'
export DEBUG='false'
export PORT='5000'
```

### 3. Run Application

```bash
# Development
python app.py

# Production (with gunicorn)
gunicorn app:app --bind 0.0.0.0:5000
```

## 📦 Module Documentation

### config.py

Centralized configuration management. Contains all environment variables, constants, and application settings.

**Key configurations:**

- Timezone (Asia/Bangkok)
- Google Sheets settings
- LINE API settings
- Risk assessment parameters

### utils/parsers.py

Utility functions for parsing and normalizing various input formats.

**Functions:**

- `parse_date_iso()` - Parse date strings
- `parse_time_hhmm()` - Parse time strings
- `resolve_time_from_params()` - Resolve time from multiple sources
- `normalize_phone_number()` - Normalize phone numbers
- `is_valid_thai_mobile()` - Validate Thai mobile numbers

### database/sheets.py

Google Sheets data layer. Handles all database operations.

**Functions:**

- `get_sheet_client()` - Get Sheets client (singleton)
- `save_symptom_data()` - Save symptom reports
- `save_profile_data()` - Save risk profiles
- `save_appointment_data()` - Save appointments

### services/notification.py

LINE notification service. Handles all LINE API interactions.

**Functions:**

- `send_line_push()` - Send push notifications
- `build_symptom_notification()` - Build symptom alert messages
- `build_risk_notification()` - Build risk assessment messages
- `build_appointment_notification()` - Build appointment messages

### services/risk_assessment.py

Risk assessment business logic. Contains all risk calculation algorithms.

**Functions:**

- `calculate_symptom_risk()` - Symptom-based risk scoring
- `normalize_diseases()` - Disease name normalization
- `calculate_personal_risk()` - Demographics-based risk scoring

### services/appointment.py

Appointment management service. Handles booking workflows.

**Functions:**

- `create_appointment()` - Create new appointment
- `format_thai_date()` - Format dates in Thai

### routes/webhook.py

Dialogflow webhook endpoints. Handles all API routes.

**Functions:**

- `register_routes()` - Register Flask routes
- `health_check()` - Health check endpoint
- `webhook()` - Main webhook handler
- `handle_report_symptoms()` - Handle symptom reports
- `handle_assess_risk()` - Handle risk assessment
- `handle_request_appointment()` - Handle appointments

## 🔧 Development

### Adding New Features

1. **Add new service:**

   ```python
   # services/new_feature.py
   from config import get_logger
   logger = get_logger(__name__)
   
   def new_function():
       # Your code here
       pass
   ```

2. **Register route:**

   ```python
   # routes/webhook.py
   @app.route('/new-endpoint', methods=['POST'])
   def new_endpoint():
       # Your code here
       pass
   ```

3. **Update imports:**

   ```python
   # services/__init__.py
   from .new_feature import new_function
   ```

### Code Style

- Follow PEP 8
- Use type hints where appropriate
- Add docstrings to all functions
- Keep functions small and focused
- Use meaningful variable names

### Testing

```bash
# Run tests (when implemented)
pytest

# Check code style
flake8 .

# Type checking
mypy .
```

## 📊 Data Flow

```
User (LINE) 
   ↓
Dialogflow 
   ↓
routes/webhook.py (API endpoint)
   ↓
services/* (Business logic)
   ├→ database/sheets.py (Data persistence)
   └→ services/notification.py (LINE notifications)
```

## 🔐 Security

- Never commit credentials to Git
- Use environment variables for sensitive data
- Validate all user inputs
- Use HTTPS in production
- Implement rate limiting (future)

## 📈 Monitoring

### Health Check

```bash
curl https://your-app.onrender.com/
```

Expected response:

```json
{
  "status": "ok",
  "service": "KwanNurse-Bot v3.0",
  "version": "3.0 - Perfect Core (Refactored)",
  "features": ["ReportSymptoms", "AssessRisk", "RequestAppointment"],
  "timestamp": "2026-01-03T14:30:00+07:00"
}
```

### Logs

View logs in Render Dashboard or use:

```bash
heroku logs --tail  # If using Heroku
```

## 🚀 Deployment

### Render

1. Connect GitHub repository
2. Set environment variables
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`

### Heroku

1. Create Procfile: `web: gunicorn app:app`
2. Push to Heroku
3. Set config vars

## 📝 Version History

### v3.0 (Refactored) - 2026-01-03

- ✅ Refactored codebase into modular structure
- ✅ Separated concerns (config, utils, services, routes)
- ✅ Improved maintainability and testability
- ✅ Added comprehensive documentation

### v4.2 (Phase 2 Complete) - 2026-04-23

- ✅ **P2-A Neuro symptom branch** — added `neuro_status` parameter to
  `ReportSymptoms`; `services/risk_assessment.py` now scores stroke-warning
  keywords (weakness, slurred speech, severe headache, confusion).
- ✅ **P2-B Pre-consult briefing** — `services/presession.py` builds a
  structured summary from recent symptom/risk history; auto-attached to
  nurse alerts in `services/teleconsult.py` (both standard and emergency).
- ✅ **P2-D Early-warning trend detection** — `services/early_warning.py`
  runs per-user trend analysis (rising risk, persistent fever, worsening
  wound, silence-after-high-risk, repeated high-risk) and fires daily scans
  via `services/scheduler.py`. Also invoked inline after every symptom
  report so alerts surface within minutes.
- ✅ **Fever negation bug fix** — `services/risk_assessment.py` and
  `services/early_warning.py` now check `ไม่มี / ไม่มีไข้ / ปกติ` BEFORE the
  positive `มี / ไข้ / ตัวร้อน` substrings. Regression covered in
  `test_symptom_risk.py` and `test_early_warning.py`.
- ✅ **Expanded Dialogflow training phrases** — `FreeTextSymptom`
  (2 → 10 phrases) and `RecommendKnowledge` (2 → 9 phrases) for better
  intent routing coverage.
- ✅ **Regression runner now covers 82 tests** across 7 suites
  (`test_bug_fixes`, `test_teleconsult`, `test_reminder`, `test_llm`,
  `test_symptom_risk`, `test_presession`, `test_early_warning`).

Run the full suite:

```bash
RUN_SCHEDULER=false python run_regression_tests.py
```

### v4.1 (LLM-Powered Triage & Education) - 2026-04-22

- ✅ Phase 2 P2-E: Free-text symptom triage via Gemini + rule-based fallback
  (`services/nlp.py`, intent `FreeTextSymptom`)
- ✅ Phase 2 P2-C: Personalized education recommender
  (`services/education.py`, intent `RecommendKnowledge`)
- ✅ Pluggable LLM adapter with circuit breaker and daily quota cap
  (`services/llm.py`)
- ✅ PII scrubber applied before every outbound LLM call (`utils/pii.py`)
- ✅ New regression suite `test_llm.py` (21 tests, fully mocked)

#### LLM Setup (optional)

Set these environment variables on your deploy target (Render, Railway, local):

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | yes, to enable | `none` | Set to `gemini` to enable |
| `GEMINI_API_KEY` | yes, if `gemini` | _empty_ | From Google AI Studio |
| `LLM_MODEL` | no | `gemini-2.0-flash` | Override model if needed |
| `LLM_TIMEOUT_SECONDS` | no | `8` | Webhook budget guard |
| `LLM_MAX_OUTPUT_TOKENS` | no | `500` | Cost guard |
| `LLM_DAILY_CALL_LIMIT` | no | `1000` | Soft cap per process |
| `LLM_CIRCUIT_FAILURE_THRESHOLD` | no | `3` | Consecutive fails → open |
| `LLM_CIRCUIT_COOLDOWN_SECONDS` | no | `60` | Circuit cool-off |

Leave `LLM_PROVIDER=none` to run in pure rule-based mode (safe default).

#### Dialogflow intents to add

| Intent name | Maps to handler | Purpose |
|---|---|---|
| `FreeTextSymptom` | `handle_free_text_symptom` | Patient types free-text symptoms |
| `RecommendKnowledge` | `handle_recommend_knowledge` | Personalized guide recommendations |

Both handlers fall back gracefully when `LLM_PROVIDER=none`.

### v3.0 (Perfect Core) - 2026-01-03

- ✅ Enhanced UX with detailed messages
- ✅ Improved risk assessment algorithms
- ✅ Better notification formatting
- ✅ Production-ready core features

### v2.0.1 - 2026-01-01

- ✅ Fixed intent name mismatch
- ✅ Added health check endpoint
- ✅ Fixed Google Sheets structure

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📄 License

This project is proprietary and confidential.

## 📞 Support

For issues or questions, please contact the development team.

---

**Built with ❤️ for better healthcare**
