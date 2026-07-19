# -*- coding: utf-8 -*-
"""
Patient profile orchestrator (Sprint 2 S2-3).

Builds the *effective* profile dict used by ``services.education`` to
personalize knowledge recommendations. Merges three sources by priority:

1. **Override** — fields the user supplied in the current Dialogflow turn
   (e.g. they explicitly typed "อายุ 60 เปลี่ยนข้อเข่า"). These always win.
2. **Stored profile** — the latest row in the ``PatientProfile`` sheet
   (long-lived demographics: surgery_type, sex, surgery_date).
3. **Latest RiskProfile** — derived demographics from the most recent risk
   assessment (age, diseases, BMI). This is what the bot already collects
   today via ``AssessRisk`` intent.

When the merge produces a *new* sticky field that wasn't already stored
(e.g. user just told us their surgery_type for the first time), the result
is upserted back to ``PatientProfile`` so subsequent turns pick it up
without re-asking.

A short TTL cache prevents repeated Sheet round-trips inside a single
conversation burst.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from config import LOCAL_TZ, PATIENT_CONSENT_VERSION, get_logger
from database.patient_profile import (
    read_patient_profile,
    read_patient_profile_result,
    upsert_patient_profile,
)
from services.cache import ttl_cache
from services.metrics import incr as _metric
from utils.parsers import is_valid_thai_mobile, normalize_phone_number
from utils.pii import scrub_user_id

logger = get_logger(__name__)


CACHE_KEY_PREFIX = "profile:v1"
CACHE_TTL_SECONDS = 60  # short — profile rarely changes mid-conversation

REGISTRATION_FIELD_ORDER = ("name", "hn", "citizen_id", "phone", "consent")
LAST_ACTIVE_CACHE_PREFIX = "profile:last-active:v1"
LAST_ACTIVE_THROTTLE_SECONDS = 6 * 3600

REGISTRATION_START_CACHE_PREFIX = "profile:registration-start:v1"
REGISTRATION_START_TTL_SECONDS = 120

_REGISTRATION_TRIGGER_SUBSTRINGS = (
    "ลงทะเบียน",
    "register",
)
_REGISTRATION_CANCEL_SUBSTRINGS = (
    "ยกเลิก",
    "cancel",
)


@dataclass(frozen=True)
class RegistrationUpdate:
    profile: dict[str, Any]
    missing_fields: list[str]
    invalid_fields: list[str]
    consent_declined: bool = False


@dataclass(frozen=True)
class RegistrationPromptDecision:
    prompt: bool
    reason: str = ""
    missing_fields: tuple[str, ...] = ()




@dataclass(frozen=True)
class RegistrationUpdate:
    profile: dict[str, Any]
    missing_fields: list[str]
    invalid_fields: list[str]
    consent_declined: bool = False


@dataclass(frozen=True)
class RegistrationPromptDecision:
    prompt: bool
    reason: str = ""
    missing_fields: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _coerce_age(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        # Dialogflow numbers come back as float; cast through float first.
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_diseases(value: Any) -> list[str]:
    """Accept list, comma string, or single string. Strip blanks."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(d).strip() for d in value if str(d).strip()]
    text = str(value).replace(",", " ")
    return [d.strip() for d in text.split() if d.strip()]


def _clean_text(value: Any, max_len: int) -> str:
    if value in (None, ""):
        return ""
    return " ".join(str(value).strip().split())[:max_len]


def mask_phone_number(phone: Any) -> str:
    normalized = normalize_phone_number(phone)
    if not normalized or not is_valid_thai_mobile(normalized):
        return ""
    return f"{normalized[:2]}X-XXX-{normalized[-4:]}"


def normalize_registration_phone(value: Any) -> tuple[str, bool]:
    if value in (None, ""):
        return "", False
    normalized = normalize_phone_number(value)
    if normalized and is_valid_thai_mobile(normalized):
        return normalized, False
    return "", True


def parse_consent_value(value: Any) -> Optional[bool]:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    negatives = {"ไม่ยินยอม", "ไม่ตกลง", "no", "decline", "false", "0", "ไม่"}
    positives = {"ยินยอม", "ตกลง", "yes", "agree", "true", "1"}
    if text in negatives:
        return False
    if text in positives:
        return True
    return None


def extract_explicit_consent(params: Optional[dict[str, Any]]) -> Optional[bool]:
    if not params:
        return None
    for key in ("consent", "patient_consent", "privacy_consent", "registration_consent"):
        if key in params:
            parsed = parse_consent_value(params.get(key))
            if parsed is not None:
                return parsed
    return None


def is_valid_thai_citizen_id(cid: Any) -> bool:
    cleaned = "".join(ch for ch in str(cid) if ch.isdigit())
    if len(cleaned) != 13:
        return False
    try:
        digits = [int(ch) for ch in cleaned]
        total = sum(digits[i] * (13 - i) for i in range(12))
        chk = (11 - (total % 11)) % 10
        return chk == digits[12]
    except Exception:
        return False


def registration_missing_fields(profile: Optional[dict[str, Any]]) -> list[str]:
    profile = profile or {}
    missing: list[str] = []
    if not (profile.get("first_name") or "").strip() or not (profile.get("last_name") or "").strip():
        missing.append("name")
    if not (profile.get("hn") or "").strip():
        missing.append("hn")
    cid = (profile.get("citizen_id") or "").strip()
    if not cid or not is_valid_thai_citizen_id(cid):
        missing.append("citizen_id")
    phone = profile.get("phone") or ""
    if not (phone and is_valid_thai_mobile(str(phone))):
        missing.append("phone")
    if (
        (profile.get("consent_version") or "") != PATIENT_CONSENT_VERSION
        or not (profile.get("consent_at") or "")
    ):
        missing.append("consent")
    return missing


def is_registration_complete(profile: Optional[dict[str, Any]]) -> bool:
    return not registration_missing_fields(profile)


def prepare_registration_update(
    existing: Optional[dict[str, Any]],
    params: Optional[dict[str, Any]],
) -> RegistrationUpdate:
    merged = dict(existing or {})
    normalized_identity = normalize_identity_fields(params)
    merged.update(normalized_identity)

    invalid: list[str] = []
    if params and any(k in params for k in ("phone", "phone_number", "phone-number", "tel")):
        raw_phone = (
            params.get("phone")
            or params.get("phone_number")
            or params.get("phone-number")
            or params.get("tel")
        )
        phone, bad = normalize_registration_phone(raw_phone)
        if bad:
            invalid.append("phone")
        elif phone:
            merged["phone"] = phone

    if params and any(k in params for k in ("citizen_id", "citizen-id", "national_id", "national-id")):
        raw_cid = (
            params.get("citizen_id")
            or params.get("citizen-id")
            or params.get("national_id")
            or params.get("national-id")
        )
        if raw_cid:
            cleaned_cid = "".join(ch for ch in str(raw_cid) if ch.isdigit())
            if not is_valid_thai_citizen_id(cleaned_cid):
                invalid.append("citizen_id")
            else:
                merged["citizen_id"] = cleaned_cid

    consent = extract_explicit_consent(params)
    consent_declined = consent is False
    if consent is True:
        merged["consent_granted"] = True
        merged["consent_version"] = PATIENT_CONSENT_VERSION

    missing = registration_missing_fields(merged)
    if merged.get("consent_granted") is True and "consent" in missing:
        missing.remove("consent")

    if not missing:
        merged["registration_status"] = "registered"
    else:
        merged["registration_status"] = "incomplete"

    return RegistrationUpdate(
        profile=merged,
        missing_fields=missing,
        invalid_fields=invalid,
        consent_declined=consent_declined,
    )


def should_prompt_registration(user_id: str) -> RegistrationPromptDecision:
    if not user_id:
        return RegistrationPromptDecision(False, "missing_user")
    result = read_patient_profile_result(user_id)
    if not result.available:
        return RegistrationPromptDecision(False, "storage_unavailable")
    missing = registration_missing_fields(result.profile)
    if missing:
        return RegistrationPromptDecision(True, "incomplete", tuple(missing))
    return RegistrationPromptDecision(False, "complete")


def touch_last_active(user_id: str, now: Optional[datetime] = None) -> bool:
    """Best-effort activity timestamp update for existing profiles only."""
    if not user_id:
        return False
    throttle_key = f"{LAST_ACTIVE_CACHE_PREFIX}:{user_id}"
    if ttl_cache.get(throttle_key) is not None:
        _metric("patient_registration.last_active_skipped")
        return False
    try:
        now = now or datetime.now(tz=LOCAL_TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=LOCAL_TZ)
        result = read_patient_profile_result(user_id)
        if not result.available or not result.profile:
            _metric("patient_registration.last_active_skipped")
            return False
        profile = dict(result.profile)
        last_raw = profile.get("last_active_at") or ""
        if last_raw:
            try:
                last = datetime.strptime(last_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
                age_seconds = max(0.0, (now - last).total_seconds())
                if age_seconds < LAST_ACTIVE_THROTTLE_SECONDS:
                    ttl_cache.set(
                        throttle_key,
                        True,
                        max(1.0, LAST_ACTIVE_THROTTLE_SECONDS - age_seconds),
                    )
                    _metric("patient_registration.last_active_skipped")
                    return False
            except ValueError:
                pass
        profile["last_active_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        ok = upsert_patient_profile(user_id, profile)
        if ok:
            invalidate_profile_cache(user_id)
            ttl_cache.set(throttle_key, True, LAST_ACTIVE_THROTTLE_SECONDS)
            _metric("patient_registration.last_active_updated")
            
            # Schedule milestone surveys if registered
            if profile.get("registration_status") == "registered":
                try:
                    from services.survey import schedule_milestone_surveys
                    schedule_milestone_surveys(user_id, now)
                except Exception:
                    logger.exception("Failed to schedule milestone surveys for user=%s", scrub_user_id(user_id))
        else:
            _metric("patient_registration.last_active_failed")
        return ok
    except Exception:
        logger.exception("touch_last_active failed user_id=%s", scrub_user_id(user_id))
        _metric("patient_registration.last_active_failed")
        return False


def mark_last_active_throttled(user_id: str) -> None:
    """Mark activity as freshly written by another profile upsert."""
    if user_id:
        ttl_cache.set(f"{LAST_ACTIVE_CACHE_PREFIX}:{user_id}", True, LAST_ACTIVE_THROTTLE_SECONDS)


def _coerce_string(value: Any) -> str:
    """Recursively unpack and coerce a value (such as a Dialogflow person struct) to string."""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "given-name", "formatted", "family-name", "first_name", "last_name"):
            sub = value.get(key)
            if sub:
                val = _coerce_string(sub)
                if val:
                    return val
        for v in value.values():
            val = _coerce_string(v)
            if val:
                return val
    return str(value)


def is_registration_trigger_text(text: Any) -> bool:
    norm = str(text or "").strip().lower()
    if not norm:
        return False
    return any(token in norm for token in _REGISTRATION_TRIGGER_SUBSTRINGS)


def is_registration_cancel_text(text: Any) -> bool:
    norm = str(text or "").strip().lower()
    if not norm:
        return False
    return any(token in norm for token in _REGISTRATION_CANCEL_SUBSTRINGS)


def mark_registration_started(user_id: str) -> None:
    if user_id:
        ttl_cache.set(
            f"{REGISTRATION_START_CACHE_PREFIX}:{user_id}",
            True,
            REGISTRATION_START_TTL_SECONDS,
        )


def has_recent_registration_start(user_id: str) -> bool:
    if not user_id:
        return False
    return ttl_cache.get(f"{REGISTRATION_START_CACHE_PREFIX}:{user_id}") is not None


def _split_person_name(text: str) -> tuple[str, str]:
    cleaned = _clean_text(text, 161)
    if not cleaned:
        return "", ""
    parts = cleaned.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return cleaned, ""


def _looks_like_hn(text: str) -> bool:
    candidate = _clean_text(text, 40).upper()
    if not candidate or " " in candidate:
        return False
    if candidate.startswith("HN") and any(ch.isdigit() for ch in candidate):
        return True
    return candidate.isalnum() and any(ch.isdigit() for ch in candidate) and len(candidate) <= 20


def _phone_param_present(params: Optional[dict[str, Any]]) -> bool:
    if not params:
        return False
    return any(key in params for key in ("phone", "phone_number", "phone-number", "tel"))


def enrich_registration_params(
    existing: Optional[dict[str, Any]],
    params: Optional[dict[str, Any]],
    query_text: str = "",
) -> dict[str, Any]:
    """
    Fill registration slots from ``query_text`` when Dialogflow returns empty
    parameters during active PatientIdentity slot-filling.
    """
    enriched = dict(params or {})
    text = _clean_text(query_text, 161)
    if not text or is_registration_trigger_text(text) or is_registration_cancel_text(text):
        return enriched

    state = dict(existing or {})
    state.update(normalize_identity_fields(enriched))

    if parse_consent_value(text) is not None:
        enriched.setdefault("consent", text)
        return enriched

    if not state.get("phone") and not _phone_param_present(enriched):
        phone, bad = normalize_registration_phone(text)
        if phone and not bad:
            enriched["phone"] = phone
            return enriched

    # Read base first/last from existing profile to determine which field we are actually prompt-filling
    db_state = dict(existing or {})
    first = (db_state.get("first_name") or "").strip()
    last = (db_state.get("last_name") or "").strip()
    hn = (db_state.get("hn") or "").strip()
    citizen_id = (db_state.get("citizen_id") or "").strip()

    if not first or not last:
        given, family = _split_person_name(text)
        if given:
            enriched.setdefault("first_name", given)
        if family:
            enriched.setdefault("last_name", family)
        return enriched

    if not hn and _looks_like_hn(text):
        enriched.setdefault("hn", text.strip().upper())
        return enriched

    if not citizen_id:
        cid_clean = "".join(ch for ch in text if ch.isdigit())
        if len(cid_clean) == 13:
            enriched.setdefault("citizen_id", cid_clean)
            return enriched

    return enriched


def clear_registration_identity_fields(user_id: str) -> bool:
    """Drop in-progress registration identity fields; keep demographics."""
    if not user_id:
        return False
    result = read_patient_profile_result(user_id)
    if not result.available or not result.profile:
        return False
    ok = upsert_patient_profile(user_id, {
        "first_name": "",
        "last_name": "",
        "hn": "",
        "citizen_id": "",
        "phone": "",
    })
    if ok:
        invalidate_profile_cache(user_id)
    return ok


def normalize_identity_fields(params: Optional[dict[str, Any]]) -> dict[str, str]:
    """Normalize patient identity fields from Dialogflow/form params."""
    if not params:
        return {}
    first_name = _coerce_string(
        params.get("first_name")
        or params.get("patient_first_name")
        or params.get("given_name")
    )
    last_name = _coerce_string(
        params.get("last_name")
        or params.get("patient_last_name")
        or params.get("family_name")
    )
    hn = _coerce_string(
        params.get("hn")
        or params.get("HN")
        or params.get("hospital_number")
        or params.get("hospital_no")
    )
    citizen_id = _coerce_string(
        params.get("citizen_id")
        or params.get("citizen-id")
        or params.get("national_id")
        or params.get("national-id")
    )
    out: dict[str, str] = {}
    first = _clean_text(first_name, 80)
    last = _clean_text(last_name, 80)
    
    if first:
        given, family = _split_person_name(first)
        if family:
            first = given
            if not last:
                last = family

    hn_norm = _clean_text(hn, 40).upper()
    cid_norm = "".join(ch for ch in str(citizen_id) if ch.isdigit())
    if first:
        out["first_name"] = first
    if last:
        out["last_name"] = last
    if hn_norm:
        out["hn"] = hn_norm
    if cid_norm:
        out["citizen_id"] = cid_norm
    return out


def _normalize_override(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Pull only the recognized profile fields out of Dialogflow params."""
    if not params:
        return {}
    surgery = (
        params.get("surgery_type")
        or params.get("surgery")
        or params.get("operation")
        or ""
    )
    sex = params.get("sex") or params.get("gender") or ""
    diseases = params.get("diseases") or params.get("disease")
    surgery_date = params.get("surgery_date") or params.get("operation_date") or ""

    out: dict[str, Any] = {}
    age = _coerce_age(params.get("age"))
    if age is not None:
        out["age"] = age
    if str(sex).strip():
        out["sex"] = str(sex).strip().lower()
    if str(surgery).strip():
        out["surgery_type"] = str(surgery).strip().lower()
    if str(surgery_date).strip():
        out["surgery_date"] = str(surgery_date).strip()
    coerced_diseases = _coerce_diseases(diseases)
    if coerced_diseases:
        out["diseases"] = coerced_diseases
    out.update(normalize_identity_fields(params))
    return out


def _load_latest_risk(user_id: str) -> dict[str, Any]:
    """
    Read demographics from the most recent ``RiskProfile`` row.

    Returns at most {age, diseases}. Never raises — a missing sheet or row
    yields ``{}`` and the caller proceeds without these fields.
    """
    try:
        from config import SHEET_RISK_PROFILE
        from database.sheets import get_worksheet
    except ImportError:
        return {}
    try:
        sheet = get_worksheet(SHEET_RISK_PROFILE)
        if not sheet:
            return {}
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return {}
        headers = values[0]
        idx_uid = headers.index("User_ID") if "User_ID" in headers else 1
        idx_age = headers.index("Age") if "Age" in headers else 2
        idx_diseases = headers.index("Diseases") if "Diseases" in headers else 6

        for row in reversed(values[1:]):
            if len(row) > idx_uid and row[idx_uid] == user_id:
                age_raw = row[idx_age] if len(row) > idx_age else ""
                diseases_raw = row[idx_diseases] if len(row) > idx_diseases else ""
                out: dict[str, Any] = {}
                age = _coerce_age(age_raw)
                if age is not None:
                    out["age"] = age
                diseases = _coerce_diseases(diseases_raw)
                if diseases:
                    out["diseases"] = diseases
                return out
        return {}
    except Exception:
        logger.exception("_load_latest_risk failed user_id=%s", user_id)
        return {}


def _merge(
    stored: Optional[dict[str, Any]],
    risk: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Apply the override > stored > risk priority order."""
    merged: dict[str, Any] = {}
    # RiskProfile baseline
    for key in ("age", "diseases"):
        if risk.get(key):
            merged[key] = risk[key]
    # Stored sticky
    for key in ("age", "sex", "surgery_type", "surgery_date", "diseases",
                "first_name", "last_name", "hn", "display_name", "display_label"):
        if stored and stored.get(key):
            merged[key] = stored[key]
    # Override always wins
    for key, val in override.items():
        merged[key] = val
    return merged


def _diff_for_persist(
    stored: Optional[dict[str, Any]],
    merged: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """
    Decide whether merge produced any *sticky* field worth writing back.

    Sticky fields = sex / surgery_type / surgery_date — they don't change
    between sessions so persisting them prevents re-asking. ``age`` and
    ``diseases`` already live in RiskProfile and are time-stamped there;
    we still copy them into PatientProfile if they're new, but they don't
    *force* a write on their own.
    """
    sticky = ("sex", "surgery_type", "surgery_date", "first_name", "last_name", "hn")
    stored = stored or {}
    new_sticky = any(
        merged.get(k) and stored.get(k) != merged.get(k)
        for k in sticky
    )
    if not new_sticky:
        return None
    # Always write the full merged record (upsert rewrites the row).
    return {
        "age": merged.get("age"),
        "sex": merged.get("sex"),
        "surgery_type": merged.get("surgery_type"),
        "surgery_date": merged.get("surgery_date"),
        "diseases": merged.get("diseases"),
        "first_name": merged.get("first_name"),
        "last_name": merged.get("last_name"),
        "hn": merged.get("hn"),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_or_build_profile(
    user_id: str,
    override_params: Optional[dict[str, Any]] = None,
    *,
    persist: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Compose the effective profile for ``user_id``.

    Args:
        user_id: LINE user id (anything falsy bypasses caching/storage).
        override_params: Raw Dialogflow ``params`` from the current turn.
            Recognized keys: age, sex, gender, surgery_type / surgery /
            operation, surgery_date / operation_date, diseases / disease.
        persist: When True (default), newly discovered sticky fields are
            upserted to ``PatientProfile`` so future calls get them for free.
        force_refresh: Skip the cache lookup. The cache key is keyed on
            ``user_id`` only; override_params do not invalidate it because
            they're applied on top each call.

    Returns:
        dict with any subset of {age, sex, surgery_type, surgery_date,
        diseases, source}. ``source`` is a debug string — comma-joined names
        of layers that contributed (``override``, ``stored``, ``risk``).
        Always returns a dict, never None.
    """
    override = _normalize_override(override_params)

    if not user_id:
        # Anonymous turn — return what the user just said, no storage.
        if override:
            override["source"] = "override"
        return override

    cache_key = f"{CACHE_KEY_PREFIX}:{user_id}"
    cached: Optional[dict[str, Any]] = None
    if not force_refresh:
        cached = ttl_cache.get(cache_key)
        if cached is not None:
            _metric("profile.cache_hit")
            # Apply override on top of cached merged profile each call.
            merged = dict(cached)
            for k, v in override.items():
                merged[k] = v
            return merged

    _metric("profile.cache_miss")

    stored = read_patient_profile(user_id)
    risk = _load_latest_risk(user_id)

    merged = _merge(stored, risk, override)
    sources = []
    if override:
        sources.append("override")
    if stored:
        sources.append("stored")
    if risk:
        sources.append("risk")
    merged["source"] = ",".join(sources) if sources else "empty"

    # Persist newly discovered sticky info — but only when we have at least
    # one such field (no point writing an all-empty row).
    if persist:
        to_write = _diff_for_persist(stored, merged)
        if to_write:
            ok = upsert_patient_profile(user_id, to_write)
            if ok:
                _metric("profile.upsert_success")
            else:
                _metric("profile.upsert_failed")

    # Cache the *base* merge (stored + risk only — without override) so
    # different overrides on subsequent turns still get the latest stickies.
    base = dict(merged)
    for k in override.keys():
        # Only strip overrides that came purely from this turn AND weren't
        # already stored — otherwise overrides like a re-stated age remain.
        if not (stored and stored.get(k)) and not risk.get(k):
            base.pop(k, None)
    ttl_cache.set(cache_key, base, CACHE_TTL_SECONDS)
    return merged


def invalidate_profile_cache(user_id: str = "") -> int:
    """Drop cached profile(s). With empty user_id, drops everything."""
    if user_id:
        ttl_cache.invalidate(f"{CACHE_KEY_PREFIX}:{user_id}")
        return 1
    return ttl_cache.invalidate_prefix(f"{CACHE_KEY_PREFIX}:")


# ---------------------------------------------------------------------------
# KWN-06: Registration Quick Reply and Flex UX helpers
# ---------------------------------------------------------------------------

#: Ordered list of fields shown in registration prompts and their Thai labels.
_REGISTRATION_FIELD_LABELS: dict[str, str] = {
    "first_name": "ชื่อ",
    "last_name":  "นามสกุล",
    "hn":         "HN (เลขบัตรผู้ป่วย)",
    "phone":      "เบอร์โทรศัพท์",
    "consent":    "ยินยอมให้ใช้ข้อมูล",
}

#: Quick-reply labels for the consent field specifically.
_CONSENT_ITEMS_LABELS = [("ยินยอม ✅", "ยินยอม"), ("ไม่ยินยอม ❌", "ไม่ยินยอม")]


def build_registration_quick_replies(missing_fields: list[str]) -> list[dict]:
    """
    Build Quick Reply button items for the next missing registration field.

    Shows helpful shortcut answers for the *first* missing field only, so the
    conversation remains one-step-at-a-time.  Falls back to an empty list when
    no missing field matches a supported quick-reply pattern.

    The consent field gets a bespoke Yes/No button pair.

    Args:
        missing_fields: Ordered list of field names that are still required.

    Returns:
        list[dict]: LINE Quick Reply item dicts (empty list = no quick reply).
    """
    from services.line_message import quick_reply_item  # deferred to avoid circular import at module load

    if not missing_fields:
        return []

    next_field = missing_fields[0]

    if next_field == "consent":
        return [quick_reply_item(label, text) for label, text in _CONSENT_ITEMS_LABELS]

    # Surgery type helper options
    if next_field == "surgery_type":
        options = [
            ("ผ่าตัดข้อเข่า", "เปลี่ยนข้อเข่า"),
            ("ผ่าตัดข้อสะโพก", "เปลี่ยนข้อสะโพก"),
            ("ผ่าตัดอื่นๆ", "อื่นๆ"),
        ]
        return [quick_reply_item(label, text) for label, text in options]

    # Generic: no pre-built options for name/HN/phone
    return []


def build_profile_flex_summary(profile: dict) -> dict:
    """
    Build a Flex bubble that summarises a patient's registration status.
    Replicated to match the premium card design from image_09929a.png precisely.
    """
    if not isinstance(profile, dict):
        profile = {}
    from services.line_message import build_flex_message  # deferred to avoid circular import

    # Safe field extraction
    first_name = profile.get("first_name") or ""
    last_name = profile.get("last_name") or ""
    full_name = f"{first_name} {last_name}".strip() or "—"
    hn = profile.get("hn") or "—"
    phone_raw = profile.get("phone") or ""
    phone_display = mask_phone_number(phone_raw) if phone_raw else "—"
    status = profile.get("registration_status") or "incomplete"
    
    citizen_id_raw = profile.get("citizen_id") or ""
    if citizen_id_raw:
        digits = "".join(ch for ch in str(citizen_id_raw) if ch.isdigit())
        if len(digits) == 13:
            citizen_id_display = f"{digits[0]}-{digits[1:5]}-XXXXX-XX-{digits[-1]}"
        else:
            citizen_id_display = digits
    else:
        citizen_id_display = "—"
        
    consent_version = profile.get("consent_version") or ""
    consent_at = profile.get("consent_at") or ""
    
    if consent_version == PATIENT_CONSENT_VERSION and bool(consent_at):
        consent_text = "ยินยอมแล้ว ✅"
    elif consent_version or consent_at:
        consent_text = "ไม่ยินยอม ❌"
    else:
        consent_text = "ยังไม่ระบุ"

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#466b96",
            "contents": [
                {
                    "type": "text",
                    "text": "📋 ข้อมูลการลงทะเบียน",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "backgroundColor": "#E6F4EA" if status == "registered" else "#FEF7E0",
                            "cornerRadius": "20px",
                            "paddingAll": "4px",
                            "paddingStart": "10px",
                            "paddingEnd": "10px",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "✅ ลงทะเบียนแล้ว" if status == "registered" else "⏳ ยังลงทะเบียนไม่ครบ",
                                    "color": "#137333" if status == "registered" else "#B06000",
                                    "size": "xs",
                                    "weight": "bold"
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": "separator",
                    "margin": "md"
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "alignItems": "center",
                    "contents": [
                        {
                            "type": "text",
                            "text": "👤",
                            "flex": 0,
                            "size": "md"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "Patient Name",
                                    "size": "xs",
                                    "color": "#aaaaaa"
                                },
                                {
                                    "type": "text",
                                    "text": full_name,
                                    "size": "sm",
                                    "weight": "bold",
                                    "color": "#111111"
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "alignItems": "center",
                    "contents": [
                        {
                            "type": "text",
                            "text": "🏥",
                            "flex": 0,
                            "size": "md"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "HN",
                                    "size": "xs",
                                    "color": "#aaaaaa"
                                },
                                {
                                    "type": "text",
                                    "text": hn,
                                    "size": "sm",
                                    "weight": "bold",
                                    "color": "#111111"
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "alignItems": "center",
                    "contents": [
                        {
                            "type": "text",
                            "text": "🆔",
                            "flex": 0,
                            "size": "md"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "Citizen ID",
                                    "size": "xs",
                                    "color": "#aaaaaa"
                                },
                                {
                                    "type": "text",
                                    "text": citizen_id_display,
                                    "size": "sm",
                                    "weight": "bold",
                                    "color": "#111111"
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "alignItems": "center",
                    "contents": [
                        {
                            "type": "text",
                            "text": "📞",
                            "flex": 0,
                            "size": "md"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "Phone",
                                    "size": "xs",
                                    "color": "#aaaaaa"
                                },
                                {
                                    "type": "text",
                                    "text": phone_display,
                                    "size": "sm",
                                    "weight": "bold",
                                    "color": "#111111"
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "alignItems": "center",
                    "contents": [
                        {
                            "type": "text",
                            "text": "👍",
                            "flex": 0,
                            "size": "md"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "Consent",
                                    "size": "xs",
                                    "color": "#aaaaaa"
                                },
                                {
                                    "type": "text",
                                    "text": consent_text,
                                    "size": "sm",
                                    "weight": "bold",
                                    "color": "#111111"
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "✏️ แก้ไขชื่อ-นามสกุล",
                        "text": "แก้ไขชื่อ"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "✏️ แก้ไขเลข HN",
                        "text": "แก้ไข HN"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "✏️ แก้ไขเลขบัตรประชาชน",
                        "text": "แก้ไขเลขบัตรประชาชน"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "✏️ แก้ไขเบอร์โทรศัพท์",
                        "text": "แก้ไขเบอร์โทร"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "✏️ แก้ไขข้อมูลทั้งหมด",
                        "text": "แก้ไขข้อมูล"
                    }
                }
            ]
        }
    }
    return build_flex_message("สรุปข้อมูลการลงทะเบียนของคุณ", bubble)
