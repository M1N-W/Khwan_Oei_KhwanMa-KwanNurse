# -*- coding: utf-8 -*-
"""
Lightweight TH/EN internationalization (Phase 5 P5-3).

Scope decision: bot replies to LINE users only. The nurse dashboard
remains Thai-only because nurses are local hospital staff. Translations
cover the ~15 most user-facing strings: triage messages, voice replies,
and generic error fallbacks. Knowledge content (long Thai medical
guides) is intentionally NOT translated in this PR — that needs
clinical review of EN copy first.

Language detection is on-the-fly per message (no persistent preference).
Heuristic: if the message contains any character in the Thai Unicode
block (U+0E00–U+0E7F) we treat it as Thai; otherwise English. This
covers the realistic mixed-message case ("ปวด head" → Thai wins) and
avoids the cost of a stored preference + UI to toggle it. Patients who
want a different language just write in that language.

Public API:

    from services.i18n import detect_language, t

    lang = detect_language("I have a headache")  # → "en"
    msg  = t("triage.high", lang, score=9)
"""
from __future__ import annotations

import re
from typing import Any

from config import get_logger

logger = get_logger(__name__)


# Thai Unicode block — any character in this range marks the message as Thai.
_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")

DEFAULT_LANG = "th"
SUPPORTED_LANGS = ("th", "en")


def detect_language(text: str) -> str:
    """
    Detect ``th`` vs ``en`` from a free-text message.

    Falls back to ``DEFAULT_LANG`` (th) for empty/None input — the bot's
    primary audience is Thai patients, so an empty input most likely
    came from a Thai user who triggered an intent without text.
    """
    if not text:
        return DEFAULT_LANG
    if _THAI_RE.search(text):
        return "th"
    # Heuristic for English-only: at least one ASCII letter present.
    # Pure-numeric or pure-emoji messages stay on default Thai.
    if re.search(r"[A-Za-z]", text):
        return "en"
    return DEFAULT_LANG


def normalize_lang(lang: str | None) -> str:
    """Coerce ``lang`` to a supported code; fall back to default."""
    if not lang:
        return DEFAULT_LANG
    code = lang.strip().lower()[:2]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


# -----------------------------------------------------------------------------
# Translation catalog
#
# Key naming: ``<area>.<intent>``. Format strings use Python ``str.format``
# placeholders (e.g. ``{score}``) so callers pass kwargs as ``t(key, lang,
# score=9)``. Missing placeholder values render as the literal ``{name}``
# rather than crashing — defensive default since these go to end users.
# -----------------------------------------------------------------------------
_TRANSLATIONS: dict[str, dict[str, str]] = {
    # ---- Triage messages (replaces hard-coded Thai in services.nlp) -----
    "triage.high": {
        "th": "🔴 ความเสี่ยงสูง (คะแนน {score}/10) "
              "กรุณาติดต่อพยาบาลทันที หรือไปโรงพยาบาลใกล้บ้าน",
        "en": "🔴 HIGH risk (score {score}/10). "
              "Please contact a nurse immediately or go to the nearest hospital.",
    },
    "triage.medium": {
        "th": "🟡 ความเสี่ยงปานกลาง (คะแนน {score}/10) "
              "ควรปรึกษาพยาบาลภายในวันนี้ และสังเกตอาการอย่างใกล้ชิด",
        "en": "🟡 Medium risk (score {score}/10). "
              "Please consult a nurse today and monitor symptoms closely.",
    },
    "triage.low": {
        "th": "🟢 ความเสี่ยงต่ำ (คะแนน {score}/10) "
              "ดูแลตัวเองตามคำแนะนำ และพักผ่อนให้เพียงพอ",
        "en": "🟢 Low risk (score {score}/10). "
              "Follow self-care guidelines and get plenty of rest.",
    },
    "triage.flags_label": {
        "th": "อาการที่พบ: {flags}",
        "en": "Detected symptoms: {flags}",
    },

    # ---- Patient identity -----------------------------------------------
    "identity.ask_first_name": {
        "th": "ขอทราบชื่อจริงของคนไข้ค่ะ",
        "en": "Please tell me the patient's first name.",
    },
    "identity.ask_last_name": {
        "th": "ขอทราบนามสกุลของคนไข้ค่ะ",
        "en": "Please tell me the patient's last name.",
    },
    "identity.ask_hn": {
        "th": "ขอทราบ HN (Hospital Number) ของคนไข้ค่ะ",
        "en": "Please tell me the patient's HN (Hospital Number).",
    },
    "identity.confirm": {
        "th": "✅ บันทึกข้อมูลคนไข้แล้วค่ะ\n\nชื่อ: {first_name} {last_name}\nHN: {hn}",
        "en": "✅ Patient identity saved.\n\nName: {first_name} {last_name}\nHN: {hn}",
    },
    "identity.save_error": {
        "th": "ขอโทษค่ะ ไม่สามารถบันทึกข้อมูลคนไข้ได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
        "en": "Sorry, I couldn't save the patient identity right now. Please try again.",
    },
    "dashboard.no_data": {
        "th": "ยังไม่มีข้อมูล",
        "en": "No data yet",
    },

    # ---- Voice STT (services.voice) -------------------------------------
    "voice.heard_prefix": {
        "th": "🎙️ ได้ยินว่า:\n\"{text}\"",
        "en": "🎙️ I heard:\n\"{text}\"",
    },
    "voice.download_fail": {
        "th": "ไม่สามารถดาวน์โหลดเสียงได้ กรุณาลองส่งใหม่อีกครั้งค่ะ",
        "en": "Couldn't download the voice message. Please try sending it again.",
    },
    "voice.transcribe_fail": {
        "th": "ขอโทษค่ะ ระบบถอดความเสียงขัดข้องชั่วคราว "
              "กรุณาพิมพ์ข้อความหรือส่งเสียงใหม่อีกครั้ง",
        "en": "Sorry, the transcription service is temporarily unavailable. "
              "Please type your message or try sending the voice note again.",
    },
    "voice.unintelligible": {
        "th": "ขอโทษค่ะ ฟังเสียงไม่ชัดเจน "
              "กรุณาพูดใหม่อีกครั้งหรือพิมพ์ข้อความค่ะ",
        "en": "Sorry, the audio isn't clear. "
              "Please speak again or type your message.",
    },
    "voice.no_message_id": {
        "th": "ไม่พบรหัสข้อความเสียง กรุณาลองใหม่อีกครั้ง",
        "en": "Voice message ID not found. Please try again.",
    },

    # ---- Webhook generic errors -----------------------------------------
    "webhook.dispatch_error": {
        "th": "ขอโทษค่ะ ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง",
        "en": "Sorry, the system is temporarily unavailable. Please try again.",
    },
    "webhook.parse_error": {
        "th": "เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้ง",
        "en": "Failed to process the request. Please try again.",
    },
}


def t(key: str, lang: str | None = None, **fmt: Any) -> str:
    """
    Look up a translation by ``key`` for ``lang``.

    - Falls back to Thai when ``lang`` is unsupported / None.
    - Falls back to the Thai string when an EN translation is missing
      so we never return an empty user-facing message.
    - Missing format placeholders render as ``{name}`` literal rather
      than raising KeyError — defensive default since these strings
      reach end users.
    """
    lang_code = normalize_lang(lang)
    entry = _TRANSLATIONS.get(key)
    if entry is None:
        logger.warning("i18n: unknown translation key %r", key)
        return key  # last-resort: surface the key name

    template = entry.get(lang_code) or entry.get(DEFAULT_LANG) or key

    if not fmt:
        return template
    try:
        return template.format(**fmt)
    except (KeyError, IndexError) as exc:
        logger.warning("i18n: missing format arg for %s: %s", key, exc)
        # Defensive default: don't crash, return the unsubstituted template
        return template


def available_keys() -> list[str]:
    """Return all translation keys (used by tests for completeness checks)."""
    return list(_TRANSLATIONS.keys())
