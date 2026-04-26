# -*- coding: utf-8 -*-
"""
LLM Provider Module (Phase 2)

Thin, dependency-free LLM adapter used by `services.nlp` and
`services.education`. Keeps the rest of the codebase provider-agnostic so we
can swap Gemini for OpenAI later without touching call sites.

Design:
- One public function: `complete(system, user, **kwargs) -> str | None`.
- Returns None on disabled / rate-limited / circuit-open / failure, and
  callers must handle None with a rule-based fallback.
- Circuit breaker: consecutive failures open the circuit for a cooldown
  window to protect webhook latency.
- Daily call counter: soft cap, in-memory, resets on calendar day boundary.
"""
import base64
import json
import re
import threading
import time
from datetime import date, datetime

import requests

from config import (
    LLM_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_API_URL,
    GEMINI_DEFAULT_MODEL,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_DAILY_CALL_LIMIT,
    LLM_CIRCUIT_FAILURE_THRESHOLD,
    LLM_CIRCUIT_COOLDOWN_SECONDS,
    LLM_VISION_DAILY_CAP,
    LLM_VISION_MODEL,
    LLM_VISION_TIMEOUT_SECONDS,
    get_logger,
)
from utils.pii import scrub_pii
from services.metrics import incr as _metric

logger = get_logger(__name__)

# Matches `?key=<token>` or `&key=<token>` segments in any URL or error
# string so we can scrub the Gemini API key before logging. The Google
# REST client puts the key in the URL query string which means raw
# ``RequestException`` messages leak it into logs (security incident).
_API_KEY_QS_RE = re.compile(r"([?&])key=[^&\s]+", re.IGNORECASE)


def _redact_api_key(text):
    """Strip ``key=...`` query-string values from any string before logging."""
    if text is None:
        return text
    return _API_KEY_QS_RE.sub(r"\1key=***", str(text))


# ---------------------------------------------------------------------------
# In-memory state (per worker process)
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_state = {
    "consecutive_failures": 0,
    "circuit_open_until": 0.0,   # epoch seconds
    "call_date": date.today(),
    "calls_today": 0,
    # Vision-specific counter (S2-2). Shares the circuit breaker but has its
    # own daily quota because image calls are 5-10x costlier than text.
    "vision_calls_today": 0,
}


def is_enabled():
    """True when a provider is configured and has a valid key."""
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    return False


def _resolve_model():
    if LLM_MODEL:
        return LLM_MODEL
    if LLM_PROVIDER == "gemini":
        return GEMINI_DEFAULT_MODEL
    return ""


def _reset_daily_counter_if_needed():
    today = date.today()
    if _state["call_date"] != today:
        _state["call_date"] = today
        _state["calls_today"] = 0
        _state["vision_calls_today"] = 0


def _circuit_open():
    return time.time() < _state["circuit_open_until"]


def _register_success():
    with _state_lock:
        _state["consecutive_failures"] = 0
        _state["circuit_open_until"] = 0.0


def _register_failure():
    with _state_lock:
        _state["consecutive_failures"] += 1
        if _state["consecutive_failures"] >= LLM_CIRCUIT_FAILURE_THRESHOLD:
            _state["circuit_open_until"] = time.time() + LLM_CIRCUIT_COOLDOWN_SECONDS
            logger.warning(
                "LLM circuit OPEN for %ds after %d consecutive failures",
                LLM_CIRCUIT_COOLDOWN_SECONDS,
                _state["consecutive_failures"],
            )


def _try_consume_daily_quota():
    with _state_lock:
        _reset_daily_counter_if_needed()
        if _state["calls_today"] >= LLM_DAILY_CALL_LIMIT:
            return False
        _state["calls_today"] += 1
        return True


def _try_consume_vision_quota():
    """Vision daily quota — separate counter from text completions."""
    with _state_lock:
        _reset_daily_counter_if_needed()
        if _state["vision_calls_today"] >= LLM_VISION_DAILY_CAP:
            return False
        _state["vision_calls_today"] += 1
        return True


# ---------------------------------------------------------------------------
# Gemini adapter
# ---------------------------------------------------------------------------
def _call_gemini(system, user, max_tokens, want_json):
    """Low-level Gemini REST call. Returns text or raises."""
    model = _resolve_model()
    url = f"{GEMINI_API_URL}/{model}:generateContent?key={GEMINI_API_KEY}"

    parts = []
    if system:
        parts.append({"text": f"[SYSTEM INSTRUCTIONS]\n{system}\n\n"})
    parts.append({"text": user})

    generation_config = {
        "maxOutputTokens": max_tokens,
        "temperature": 0.2,
    }
    if want_json:
        generation_config["responseMimeType"] = "application/json"

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }

    resp = requests.post(
        url,
        json=payload,
        timeout=LLM_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"Gemini returned no candidates: {data}")
    content = candidates[0].get("content") or {}
    parts_out = content.get("parts") or []
    texts = [p.get("text", "") for p in parts_out if isinstance(p, dict)]
    return "".join(texts).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def complete(system, user, max_tokens=None, want_json=False):
    """
    Run a single LLM completion.

    Args:
        system: system/instruction text
        user: user prompt (will be PII-scrubbed before leaving the process)
        max_tokens: override LLM_MAX_OUTPUT_TOKENS
        want_json: hint the provider to return JSON-mode output

    Returns:
        str | None: response text, or None if disabled, circuit-open,
        quota-exhausted, or request failed.
    """
    if not is_enabled():
        return None

    if _circuit_open():
        logger.info("LLM skip: circuit open")
        _metric("llm.skip_circuit_open")
        return None

    if not _try_consume_daily_quota():
        logger.warning("LLM skip: daily quota exhausted (%d)", LLM_DAILY_CALL_LIMIT)
        _metric("llm.skip_quota")
        return None

    scrubbed_user = scrub_pii(user) or ""
    scrubbed_system = scrub_pii(system) if system else None
    tokens = max_tokens or LLM_MAX_OUTPUT_TOKENS

    start = time.time()
    try:
        if LLM_PROVIDER == "gemini":
            text = _call_gemini(scrubbed_system, scrubbed_user, tokens, want_json)
        else:
            return None
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info("LLM ok provider=%s elapsed=%dms chars=%d",
                    LLM_PROVIDER, elapsed_ms, len(text))
        _register_success()
        _metric("llm.call_success")
        return text
    except requests.exceptions.Timeout:
        logger.warning("LLM timeout after %.1fs", LLM_TIMEOUT_SECONDS)
        _register_failure()
        _metric("llm.call_timeout")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning("LLM network error: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.call_network_error")
        return None
    except Exception:
        logger.exception("LLM unexpected error")
        _register_failure()
        _metric("llm.call_error")
        return None


def complete_json(system, user, max_tokens=None):
    """
    Convenience wrapper: call `complete(want_json=True)` and parse JSON.
    Returns dict/list or None on any failure (including invalid JSON).
    """
    raw = complete(system, user, max_tokens=max_tokens, want_json=True)
    if not raw:
        return None
    # Some providers wrap JSON in markdown fences despite the JSON hint.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # strip possible language tag on first line
        if "\n" in cleaned:
            first_line, rest = cleaned.split("\n", 1)
            if first_line.lower().strip() in ("json", ""):
                cleaned = rest
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("LLM JSON parse failed: %s | raw=%r", e, raw[:200])
        return None


# ---------------------------------------------------------------------------
# Vision (Gemini multimodal) — Sprint 2 S2-2
# ---------------------------------------------------------------------------
def _call_gemini_vision(system, user_text, image_bytes, mime_type, max_tokens):
    """
    Low-level Gemini Vision REST call (multimodal: text + inline image).
    Returns text response or raises.
    """
    model = LLM_VISION_MODEL or _resolve_model()
    url = f"{GEMINI_API_URL}/{model}:generateContent?key={GEMINI_API_KEY}"

    parts = []
    if system:
        parts.append({"text": f"[SYSTEM INSTRUCTIONS]\n{system}\n\n"})
    if user_text:
        parts.append({"text": user_text})

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    parts.append({
        "inlineData": {
            "mimeType": mime_type or "image/jpeg",
            "data": image_b64,
        }
    })

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    resp = requests.post(
        url,
        json=payload,
        timeout=LLM_VISION_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"Gemini vision returned no candidates: {data}")
    content = candidates[0].get("content") or {}
    parts_out = content.get("parts") or []
    texts = [p.get("text", "") for p in parts_out if isinstance(p, dict)]
    return "".join(texts).strip()


def complete_image_json(system, user_text, image_bytes, mime_type="image/jpeg", max_tokens=None):
    """
    Run a single multimodal LLM completion expected to return JSON.

    Args:
        system: system/instruction text (clinical guidance for the model)
        user_text: short prompt accompanying the image
        image_bytes: raw image bytes (jpeg/png from LINE Content API)
        mime_type: MIME type of the image (default 'image/jpeg')
        max_tokens: override LLM_MAX_OUTPUT_TOKENS

    Returns:
        dict / list parsed from response, or None if disabled / circuit-open /
        quota-exhausted / request failed / response not valid JSON. Callers
        must handle None with a rule-based fallback message.
    """
    if not is_enabled() or not image_bytes:
        return None

    if _circuit_open():
        logger.info("LLM vision skip: circuit open")
        _metric("llm.vision_skip_circuit_open")
        return None

    if not _try_consume_vision_quota():
        logger.warning("LLM vision skip: daily quota exhausted (%d)", LLM_VISION_DAILY_CAP)
        _metric("llm.vision_skip_quota")
        return None

    # Note: image bytes are NOT PII-scrubbed (no text), but `system` and
    # `user_text` go through scrub_pii like the text path.
    scrubbed_system = scrub_pii(system) if system else None
    scrubbed_user = scrub_pii(user_text) if user_text else ""
    tokens = max_tokens or LLM_MAX_OUTPUT_TOKENS

    start = time.time()
    try:
        if LLM_PROVIDER == "gemini":
            raw = _call_gemini_vision(scrubbed_system, scrubbed_user, image_bytes, mime_type, tokens)
        else:
            return None
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "LLM vision ok provider=%s elapsed=%dms image_kb=%d chars=%d",
            LLM_PROVIDER, elapsed_ms, len(image_bytes) // 1024, len(raw),
        )
        _register_success()
        _metric("llm.vision_call_success")
    except requests.exceptions.Timeout:
        logger.warning("LLM vision timeout after %.1fs", LLM_VISION_TIMEOUT_SECONDS)
        _register_failure()
        _metric("llm.vision_call_timeout")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning("LLM vision network error: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.vision_call_network_error")
        return None
    except Exception:
        logger.exception("LLM vision unexpected error")
        _register_failure()
        _metric("llm.vision_call_error")
        return None

    # Parse JSON (some providers wrap in fences despite responseMimeType hint)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first_line, rest = cleaned.split("\n", 1)
            if first_line.lower().strip() in ("json", ""):
                cleaned = rest
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("LLM vision JSON parse failed: %s | raw=%r", e, raw[:200])
        _metric("llm.vision_call_parse_error")
        return None


# Debug-only introspection used by tests.
def _get_state_snapshot():
    with _state_lock:
        return dict(_state)


def _reset_state_for_tests():
    """Test-only hook to reset circuit + counters."""
    with _state_lock:
        _state["consecutive_failures"] = 0
        _state["circuit_open_until"] = 0.0
        _state["call_date"] = date.today()
        _state["calls_today"] = 0
        _state["vision_calls_today"] = 0
