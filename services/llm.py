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
    GEMINI_API_KEYS,
    GEMINI_API_URL,
    GEMINI_DEFAULT_MODEL,
    LLM_JSON_MAX_ATTEMPTS,
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


_key_cooldowns = {}
_cooldown_lock = threading.Lock()


def is_enabled():
    """True when a provider is configured and has a valid key."""
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEYS) or bool(GEMINI_API_KEY)
    return False


def _resolve_model():
    if LLM_MODEL:
        return LLM_MODEL
    if LLM_PROVIDER == "gemini":
        return GEMINI_DEFAULT_MODEL
    return ""


def route_model(intent: str = None) -> str:
    # Use one configured model across text, vision, and audio paths. Intent
    # aliases previously rewrote requests to unavailable preview models.
    return LLM_MODEL or GEMINI_DEFAULT_MODEL


def _execute_with_key_fallback(api_call_fn, model_name):
    import random
    
    # Retrieve pool of keys, fallback to GEMINI_API_KEY if config is empty (for tests patching)
    keys_pool = GEMINI_API_KEYS
    if not keys_pool and GEMINI_API_KEY:
        keys_pool = [GEMINI_API_KEY]
        
    if not keys_pool:
        keys_pool = ["mock-key"]
        
    now = time.time()
    with _cooldown_lock:
        available_keys = [k for k in keys_pool if _key_cooldowns.get(k, 0.0) <= now]
        if not available_keys:
            # All keys are rate-limited. Do not immediately clear cooldowns
            # and send another burst to the same quota/project.
            raise requests.exceptions.RetryError(
                "All configured Gemini keys are on cooldown"
            )
            
    keys_to_try = list(available_keys)
    random.shuffle(keys_to_try)
    
    for k in keys_pool:
        if k not in keys_to_try:
            keys_to_try.append(k)
            
    last_exc = None
    for api_key in keys_to_try:
        try:
            return api_call_fn(api_key, model_name)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("Gemini 429 rate limit hit for key ...%s. Placing key on cooldown.", api_key[-6:])
                with _cooldown_lock:
                    _key_cooldowns[api_key] = time.time() + 60.0
                _metric("llm.key_fallback_429")
                last_exc = e
                continue
            status = e.response.status_code if e.response else None
            if status is not None and 400 <= status < 500 and status != 429:
                logger.error(
                    "Gemini request rejected status=%s body=%s",
                    status,
                    (e.response.text or "")[:300] if e.response is not None else "",
                )
                raise
            logger.warning("Gemini HTTP error %s for key ...%s. Retrying next key.", status or e, api_key[-6:])
            last_exc = e
            continue
        except requests.exceptions.RequestException as e:
            logger.warning("Gemini connection error for key ...%s: %s. Retrying next key.", api_key[-6:], _redact_api_key(e))
            last_exc = e
            continue
        except Exception as e:
            logger.warning("Gemini unexpected error for key ...%s: %s. Retrying next key.", api_key[-6:], _redact_api_key(e))
            last_exc = e
            continue
            
    if last_exc:
        raise last_exc


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
def _call_gemini(system, user, max_tokens, want_json, api_key, model_name):
    """Low-level Gemini REST call. Returns text or raises."""
    url = f"{GEMINI_API_URL}/{model_name}:generateContent"

    parts = []
    if system:
        parts.append({"text": f"[SYSTEM INSTRUCTIONS]\n{system}\n\n"})
    parts.append({"text": user})

    generation_config = {
        "maxOutputTokens": max_tokens,
        "temperature": 0.2,
    }
    if want_json:
        generation_config["response_mime_type"] = "application/json"

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }

    resp = requests.post(
        url,
        json=payload,
        timeout=LLM_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
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
def complete(system, user, max_tokens=None, want_json=False, intent=None):
    """
    Run a single LLM completion.

    Args:
        system: system/instruction text
        user: user prompt (will be PII-scrubbed before leaving the process)
        max_tokens: override LLM_MAX_OUTPUT_TOKENS
        want_json: hint the provider to return JSON-mode output
        intent: the Dialogflow intent string to route the target model

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
    if want_json and tokens < 300:
        tokens = 500
    model_name = route_model(intent)

    start = time.time()
    try:
        if LLM_PROVIDER == "gemini":
            api_call = lambda k, m: _call_gemini(scrubbed_system, scrubbed_user, tokens, want_json, k, m)
            text = _execute_with_key_fallback(api_call, model_name)
        else:
            return None
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info("LLM ok provider=%s model=%s elapsed=%dms chars=%d",
                    LLM_PROVIDER, model_name, elapsed_ms, len(text))
        _register_success()
        _metric("llm.call_success")
        return text
    except requests.exceptions.Timeout as e:
        logger.warning("LLM timeout: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.call_timeout")
        return "🚨 ขณะนี้ระบบ AI มีผู้ใช้งานจำนวนมากชั่วคราว หากท่านมีอาการผิดปกติหรือต้องการความช่วยเหลือด่วน กรุณาพิมพ์ 'คุยกับพยาบาล' เพื่อติดต่อพยาบาลโดยตรง หรือหากเป็นกรณีฉุกเฉิน กรุณาโทร 1669 ทันทีค่ะ"
    except requests.exceptions.RequestException as e:
        logger.warning("LLM network error: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.call_network_error")
        return "🚨 ขณะนี้ระบบ AI มีผู้ใช้งานจำนวนมากชั่วคราว หากท่านมีอาการผิดปกติหรือต้องการความช่วยเหลือด่วน กรุณาพิมพ์ 'คุยกับพยาบาล' เพื่อติดต่อพยาบาลโดยตรง หรือหากเป็นกรณีฉุกเฉิน กรุณาโทร 1669 ทันทีค่ะ"
    except Exception as e:
        logger.exception("LLM unexpected error: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.call_error")
        return "🚨 ขณะนี้ระบบ AI มีผู้ใช้งานจำนวนมากชั่วคราว หากท่านมีอาการผิดปกติหรือต้องการความช่วยเหลือด่วน กรุณาพิมพ์ 'คุยกับพยาบาล' เพื่อติดต่อพยาบาลโดยตรง หรือหากเป็นกรณีฉุกเฉิน กรุณาโทร 1669 ทันทีค่ะ"


def _parse_json_robust(raw: str):
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first_line, rest = cleaned.split("\n", 1)
            if first_line.lower().strip() in ("json", ""):
                cleaned = rest
    cleaned = cleaned.strip()
    
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
        
    import re as _re
    m = _re.search(r'(\{.*\}|\[.*\])', cleaned, _re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


def complete_json(system, user, max_tokens=None, intent=None):
    """
    Convenience wrapper: call `complete(want_json=True)` and parse JSON.
    Returns dict/list or None on any failure (including invalid JSON).
    """
    for attempt in range(max(1, LLM_JSON_MAX_ATTEMPTS)):
        raw = complete(system, user, max_tokens=max_tokens, want_json=True, intent=intent)
        if not raw or raw.startswith("🚨"):
            continue
        parsed = _parse_json_robust(raw)
        if parsed is not None:
            return parsed
        logger.warning("complete_json: attempt %d JSON parse failed. raw=%r", attempt + 1, raw[:200])
    return None


# ---------------------------------------------------------------------------
# Vision (Gemini multimodal) — Sprint 2 S2-2
# ---------------------------------------------------------------------------
def _call_gemini_vision(system, user_text, image_bytes, mime_type, max_tokens, api_key, model_name):
    """
    Low-level Gemini Vision REST call (multimodal: text + inline image).
    Returns text response or raises.
    """
    url = f"{GEMINI_API_URL}/{model_name}:generateContent"

    parts = []
    if system:
        parts.append({"text": f"[SYSTEM INSTRUCTIONS]\n{system}\n\n"})
    if user_text:
        parts.append({"text": user_text})

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    parts.append({
        "inline_data": {
            "mime_type": mime_type or "image/jpeg",
            "data": image_b64,
        }
    })

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "severity": {"type": "STRING", "enum": ["low", "medium", "high"]},
                    "observations": {"type": "ARRAY", "items": {"type": "STRING"}, "maxItems": 5},
                    "advice": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["severity", "observations", "advice", "confidence"],
            },
            # Wound triage is a short extraction task.  Reserve tokens for the
            # JSON response instead of allowing default medium reasoning to use them.
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }

    resp = requests.post(
        url,
        json=payload,
        timeout=LLM_VISION_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
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


def complete_image_json(system, user_text, image_bytes, mime_type="image/jpeg", max_tokens=None, intent=None):
    """
    Run a single multimodal LLM completion expected to return JSON.

    Args:
        system: system/instruction text (clinical guidance for the model)
        user_text: short prompt accompanying the image
        image_bytes: raw image bytes (jpeg/png from LINE Content API)
        mime_type: MIME type of the image (default 'image/jpeg')
        max_tokens: override LLM_MAX_OUTPUT_TOKENS
        intent: the Dialogflow intent string to route the target model

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
    tokens = max(max_tokens or LLM_MAX_OUTPUT_TOKENS, 1024)
    # Vision has its own safe model setting; do not inherit text-intent
    # routing or preview aliases that may not support multimodal JSON.
    model_name = LLM_VISION_MODEL or _resolve_model()

    # _execute_with_key_fallback already tries each available key once.
    # Retrying the whole key pool multiplies 429/503 traffic for one image.
    for attempt in range(1):
        start = time.time()
        try:
            if LLM_PROVIDER == "gemini":
                api_call = lambda k, m: _call_gemini_vision(scrubbed_system, scrubbed_user, image_bytes, mime_type, tokens, k, m)
                raw = _execute_with_key_fallback(api_call, model_name)
            else:
                return None
            elapsed_ms = int((time.time() - start) * 1000)
            parsed = _parse_json_robust(raw)
            if parsed is not None:
                logger.info(
                    "LLM vision ok provider=%s model=%s elapsed=%dms image_kb=%d chars=%d attempt=%d",
                    LLM_PROVIDER, model_name, elapsed_ms, len(image_bytes) // 1024, len(raw), attempt + 1,
                )
                _register_success()
                _metric("llm.vision_call_success")
                return parsed
            else:
                logger.warning("LLM vision JSON parse failed: raw=%r", raw[:200])
                _metric("llm.vision_call_parse_error")
        except requests.exceptions.Timeout as e:
            logger.warning("LLM vision timeout: %s", _redact_api_key(e))
            _register_failure()
            _metric("llm.vision_call_timeout")
        except requests.exceptions.RequestException as e:
            logger.warning("LLM vision network error: %s", _redact_api_key(e))
            _register_failure()
            _metric("llm.vision_call_network_error")
        except Exception as e:
            logger.warning("LLM vision unexpected error: %s", _redact_api_key(e))
            _register_failure()
            _metric("llm.vision_call_error")
            
    return None


# ---------------------------------------------------------------------------
# Audio transcription (Phase 5 P5-2)
#
# Gemini multimodal accepts inline audio bytes alongside a text prompt the
# same way it accepts images. We use this to transcribe LINE voice messages
# into Thai/English so the existing NLP triage pipeline (text → risk score)
# can run unchanged. Reusing Gemini means no extra API key / provider —
# just one more env-gated feature that shares the vision daily cap because
# multimodal calls have similar cost tiers.
#
# Supported MIME types per Gemini docs: audio/wav, audio/mp3, audio/aiff,
# audio/aac, audio/ogg, audio/flac. LINE actually sends m4a (audio/mp4)
# which the API also accepts in practice; we pass it through.
# ---------------------------------------------------------------------------
_TRANSCRIBE_PROMPT = (
    "You are a voice-to-text transcriber for a Thai healthcare chatbot. "
    "Transcribe the audio VERBATIM in its original language (mostly Thai, "
    "sometimes English). Do not translate, summarize, or add commentary. "
    "Output ONLY the transcription as plain text — no quotes, no JSON, "
    "no labels. If the audio is silent or unintelligible, output exactly: "
    "[ไม่สามารถถอดความได้]"
)


def _call_gemini_audio(audio_bytes, mime_type, max_tokens, api_key, model_name):
    """Low-level Gemini multimodal call for audio → text transcription."""
    url = f"{GEMINI_API_URL}/{model_name}:generateContent"

    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    parts = [
        {"text": _TRANSCRIBE_PROMPT},
        {"inline_data": {"mime_type": mime_type or "audio/mp4", "data": audio_b64}},
    ]
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.0,  # transcription should be deterministic
        },
    }

    resp = requests.post(
        url, json=payload,
        timeout=LLM_VISION_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"Gemini audio returned no candidates: {data}")
    content = candidates[0].get("content") or {}
    parts_out = content.get("parts") or []
    texts = [p.get("text", "") for p in parts_out if isinstance(p, dict)]
    return "".join(texts).strip()


def transcribe_audio(audio_bytes, mime_type="audio/mp4", *, max_tokens=None, intent=None):
    """
    Transcribe a voice message to text via Gemini multimodal.

    Args:
        audio_bytes: raw bytes from LINE Content API (typically m4a/aac).
        mime_type: e.g. ``audio/mp4`` (LINE default), ``audio/aac``,
            ``audio/wav``. Falls back to ``audio/mp4`` when unset.
        max_tokens: upper bound on transcription length (token budget).
        intent: the Dialogflow intent string to route the target model

    Returns:
        Transcribed text string, or ``None`` if disabled / circuit-open /
        quota-exhausted / API failure / empty bytes. Callers must handle
        ``None`` with a graceful Thai fallback message.

    Quota: shares the vision daily cap (``LLM_VISION_DAILY_CAP``) since
    audio multimodal requests have similar pricing to vision requests.
    """
    if not is_enabled() or not audio_bytes:
        return None

    if _circuit_open():
        logger.info("LLM audio skip: circuit open")
        _metric("llm.audio_skip_circuit_open")
        return None

    if not _try_consume_vision_quota():
        logger.warning(
            "LLM audio skip: daily multimodal quota exhausted (%d)",
            LLM_VISION_DAILY_CAP,
        )
        _metric("llm.audio_skip_quota")
        return None

    tokens = max_tokens or LLM_MAX_OUTPUT_TOKENS
    model_name = route_model(intent)

    start = time.time()
    try:
        if LLM_PROVIDER == "gemini":
            api_call = lambda k, m: _call_gemini_audio(audio_bytes, mime_type, tokens, k, m)
            raw = _execute_with_key_fallback(api_call, model_name)
        else:
            return None
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "LLM audio ok provider=%s model=%s elapsed=%dms audio_kb=%d chars=%d",
            LLM_PROVIDER, model_name, elapsed_ms, len(audio_bytes) // 1024, len(raw),
        )
        _register_success()
        _metric("llm.audio_call_success")
        return raw or None
    except requests.exceptions.Timeout as e:
        logger.warning("LLM audio timeout: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.audio_call_timeout")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning("LLM audio network error: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.audio_call_network_error")
        return None
    except Exception as e:
        logger.warning("LLM audio unexpected error: %s", _redact_api_key(e))
        _register_failure()
        _metric("llm.audio_call_error")
        return None


# Debug-only introspection used by tests.
def _get_state_snapshot():
    with _state_lock:
        return dict(_state)


def _reset_state_for_tests():
    """Test-only hook to reset circuit, counters, and key cooldowns."""
    with _state_lock:
        _state["consecutive_failures"] = 0
        _state["circuit_open_until"] = 0.0
        _state["call_date"] = date.today()
        _state["calls_today"] = 0
        _state["vision_calls_today"] = 0
    with _cooldown_lock:
        _key_cooldowns.clear()
