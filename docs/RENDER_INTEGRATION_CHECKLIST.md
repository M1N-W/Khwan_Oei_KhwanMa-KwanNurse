# Render integration checklist

This checklist is safe to use in Render without exposing credentials.

## Required environment variables

- `CHANNEL_ACCESS_TOKEN` and `LINE_CHANNEL_SECRET` for LINE replies and signature verification.
- `WORKSHEET_LINK` and either `GSPREAD_CREDENTIALS` (raw service-account JSON) or `GOOGLE_CREDS_B64` (Base64 of the same JSON).
- `LLM_PROVIDER=gemini` and at least one of `GEMINI_API_KEY`, `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, or `GEMINI_API_KEY_3` to enable Gemini. `LLM_PROVIDER=none` intentionally disables Gemini and uses rule-based fallbacks.
- `DIALOGFLOW_WEBHOOK_TOKEN` when Dialogflow calls `/webhook`.

Do not commit the service-account JSON or API keys. Add them only in Render Environment settings.

## Read-only checks after redeploy

1. `GET /` must report `can_persist_sheets: true` and, when Gemini is intended, `llm_enabled: true`.
2. `GET /readyz` must report `sheets: ok`.
3. Send a test registration message and verify a row appears in `PatientProfile`.
4. Complete a symptom report and verify `SymptomLog`; send a wound image and verify `WoundAnalysisLog`.
5. Create an appointment and verify `Appointments`; registration completion schedules the survey/reminder rows.

An empty worksheet with headers is not evidence that its feature is broken: several sheets are event/audit logs and receive rows only after the corresponding feature is used. If `/readyz` is unavailable, writes are skipped and the application logs `No Google credentials found` / `No gspread client available`.

## Routing contract

- Configure Dialogflow to call `/webhook` for text intents, or use `/line/webhook` for direct LINE mode.
- Direct LINE mode now handles text registration and the exact wound-photo commands. Image events continue through `handle_line_image_event`.
