# Render integration checklist

This checklist is safe to use in Render without exposing credentials.

## Required environment variables

- `CHANNEL_ACCESS_TOKEN` and `LINE_CHANNEL_SECRET` for LINE replies and signature verification.
- `WORKSHEET_LINK` and either `GSPREAD_CREDENTIALS` (raw service-account JSON) or `GOOGLE_CREDS_B64` (Base64 of the same JSON).
- `LLM_PROVIDER=gemini` and at least one of `GEMINI_API_KEY`, `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, or `GEMINI_API_KEY_3` to enable Gemini. `LLM_PROVIDER=none` intentionally disables Gemini and uses rule-based fallbacks.
- `DIALOGFLOW_WEBHOOK_TOKEN` when Dialogflow calls `/webhook`.
- `DIALOGFLOW_PROJECT_ID` (or a `project_id` in the Google service-account JSON),
  `DIALOGFLOW_LANGUAGE_CODE=th`, and optionally
  `DIALOGFLOW_BRIDGE_TIMEOUT_SECONDS=8` for the direct LINE text bridge.
- The Google service account must have permission to call Dialogflow ES
  `detectIntent` and the Dialogflow API must be enabled for that project.

Do not commit the service-account JSON or API keys. Add them only in Render Environment settings.

## Read-only checks after redeploy

1. `GET /` must report `can_persist_sheets: true` and, when Gemini is intended, `llm_enabled: true`.
2. `GET /readyz` must report `sheets: ok`.
3. Send a test registration message and verify a row appears in `PatientProfile`.
4. Complete a symptom report and verify `SymptomLog`; send a wound image and verify `WoundAnalysisLog`.
5. Create an appointment and verify `Appointments`; registration completion schedules the survey/reminder rows.

An empty worksheet with headers is not evidence that its feature is broken: several sheets are event/audit logs and receive rows only after the corresponding feature is used. If `/readyz` is unavailable, writes are skipped and the application logs `No Google credentials found` / `No gspread client available`.

## Routing contract

- Set the LINE Developers webhook URL to
  `https://<render-service>.onrender.com/line/webhook` and keep webhook enabled.
- `/line/webhook` verifies the LINE signature, forwards every text event to
  Dialogflow `detectIntent`, and returns Dialogflow text/LINE payloads through
  the LINE Messaging API. This is the bridge path that can send the completed
  registration profile as a Flex card.
- Image events do not go through Dialogflow: the bridge downloads the LINE
  image content and passes it to Gemini through `handle_line_image_event`.
- Keep Dialogflow fulfillment pointed at
  `https://<render-service>.onrender.com/webhook`, with the
  `Authorization: Bearer <DIALOGFLOW_WEBHOOK_TOKEN>` header. Do not configure
  Dialogflow's built-in LINE integration as a second LINE webhook, or events
  can be delivered to the old text-only path.
- The direct bridge intentionally keeps Dialogflow as the intent/entity
  classifier while the runtime webhook remains the state-machine authority.
