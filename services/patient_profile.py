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

from typing import Any, Optional

from config import get_logger
from database.patient_profile import read_patient_profile, upsert_patient_profile
from services.cache import ttl_cache
from services.metrics import incr as _metric

logger = get_logger(__name__)


CACHE_KEY_PREFIX = "profile:v1"
CACHE_TTL_SECONDS = 60  # short — profile rarely changes mid-conversation


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
    for key in ("age", "sex", "surgery_type", "surgery_date", "diseases"):
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
    sticky = ("sex", "surgery_type", "surgery_date")
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
