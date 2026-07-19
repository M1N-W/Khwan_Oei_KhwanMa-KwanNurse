# Dialogflow export checklist

## Build

Run from the repository root:

```powershell
python scripts/validate_dialogflow_export.py
python scripts/zip_dialogflow.py
```

The generated `dialogflow_agent.zip` is the import artifact. No production agent is changed by these commands.

## Import verification

After importing into a test Dialogflow ES agent, verify:

1. `ContactNurse` returns the five-item menu during office hours.
2. Bare `1`, `2`, `3`, `4`, and `5` reach the webhook and select the expected category.
3. Outside office hours, the two Quick Replies remain `รอเวลาทำการ` and `แจ้งเรื่องฉุกเฉิน`.
4. `ยกเลิก` clears consultation and appointment contexts.
5. `RequestAppointment` preserves the selected day after receiving `กันยายน`, `ก.ย.`, or another month synonym.
6. `Default Fallback Intent` is webhook-enabled so the runtime state machine can recover active flows.

Use a separate test agent/session before importing into production. Production deployment remains an explicit manual step.
