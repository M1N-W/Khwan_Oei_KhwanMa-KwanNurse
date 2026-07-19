"""Validate the Dialogflow ES export before importing it into an agent."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIALOGFLOW = ROOT / "dialogflow"
DISPATCHED = {
    "ReportSymptoms", "AssessRisk", "AssessPersonalRisk", "RequestAppointment",
    "GetKnowledge", "GetFollowUpSummary", "ContactNurse", "AfterHoursChoice",
    "CancelConsultation", "GetGroupID", "FreeTextSymptom", "RecommendKnowledge",
    "StartRegistration", "ViewMyProfile", "EditMyProfile",
}
RUNTIME_SLOT_FILLING = {
    "ReportSymptoms", "AssessRisk", "AssessPersonalRisk", "RequestAppointment",
}
PROFILE_INTENT_PHRASES = {
    "StartRegistration": {"ลงทะเบียน"},
    "ViewMyProfile": {"ข้อมูลของฉัน"},
    "EditMyProfile": {"แก้ไขข้อมูล"},
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    errors: list[str] = []
    intent_dir = DIALOGFLOW / "intents"
    entity_dir = DIALOGFLOW / "entities"

    for name in sorted(DISPATCHED):
        path = intent_dir / f"{name}.json"
        if not path.exists():
            errors.append(f"missing intent: {name}")
            continue
        intent = load_json(path)
        if not intent.get("webhookUsed"):
            errors.append(f"webhook disabled: {name}")
        if name in RUNTIME_SLOT_FILLING:
            if intent.get("webhookForSlotFilling"):
                errors.append(f"Dialogflow slot filling must be disabled: {name}")
            for response in intent.get("responses", []):
                for parameter in response.get("parameters", []):
                    if parameter.get("required"):
                        errors.append(
                            f"runtime-owned parameter must not be required: "
                            f"{name}.{parameter.get('name')}"
                        )

    fallback = load_json(intent_dir / "Default Fallback Intent.json")
    if not fallback.get("fallbackIntent") or not fallback.get("webhookUsed"):
        errors.append("Default Fallback Intent must be a webhook-enabled fallback")

    for path in sorted(entity_dir.glob("*_entries_th.json")):
        entries = load_json(path)
        values = [entry.get("value") for entry in entries]
        if len(values) != len(set(values)):
            errors.append(f"duplicate entity value: {path.name}")

    phrases_path = intent_dir / "AfterHoursChoice_usersays_th.json"
    phrases = load_json(phrases_path)
    phrase_text = {
        "".join(part.get("text", "") for part in item.get("data", []))
        for item in phrases
    }
    for required in {"1", "2", "3", "4", "5"}:
        if required not in phrase_text:
            errors.append(f"missing AfterHoursChoice phrase: {required}")

    for intent_name, required_phrases in PROFILE_INTENT_PHRASES.items():
        path = intent_dir / f"{intent_name}_usersays_th.json"
        phrases = load_json(path) if path.exists() else []
        phrase_text = {
            "".join(part.get("text", "") for part in item.get("data", []))
            for item in phrases
        }
        for required in required_phrases:
            if required not in phrase_text:
                errors.append(f"missing {intent_name} phrase: {required}")

    legacy_profile_phrases = intent_dir / "PatientIdentity_usersays_th.json"
    if legacy_profile_phrases.exists() and load_json(legacy_profile_phrases):
        errors.append("PatientIdentity must not own top-level profile phrases")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("Dialogflow export validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
