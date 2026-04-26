# -*- coding: utf-8 -*-
"""
Voice message orchestration (Phase 5 P5-2).

End-to-end flow for a LINE audio event:

  1. Download audio bytes from LINE Content API
  2. Transcribe via Gemini multimodal (``services.llm.transcribe_audio``)
  3. Run the existing free-text triage pipeline (``services.nlp``)
     so risk scoring, symptom logging, and nurse alerts behave exactly
     like a typed message
  4. Reply to the patient with what we heard + the triage result
  5. Persist a privacy-safe audit row in VoiceMessageLog

Why route through the text triage pipeline instead of building a new
voice-specific path? Two wins:

- **Consistency**: nurses see the same fields and risk levels whether the
  patient typed or spoke. No new dashboard work needed.
- **Less surface area**: voice features get free upgrades whenever
  ``analyze_free_text`` improves.

This module is **never allowed to raise**. Every step has a fallback
path so a transient Gemini failure or a malformed audio blob results in
a graceful Thai apology instead of an HTTP 500 (which LINE would retry
forever).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from config import get_logger
from services.metrics import incr

logger = get_logger(__name__)


# Marker that the transcription prompt instructs Gemini to emit when the
# audio is silent or unintelligible. Keep in sync with ``llm._TRANSCRIBE_PROMPT``.
_UNINTELLIGIBLE_MARKER = "[ไม่สามารถถอดความได้]"

# Cap stored transcription length on user reply (keep LINE bubble readable).
_REPLY_TRANSCRIPT_PREVIEW = 200


def _short_user(user_id: str) -> str:
    if len(user_id or "") > 10:
        return user_id[:4] + "***" + user_id[-4:]
    return "***"


def handle_voice_event(event: Dict[str, Any]) -> None:
    """
    Process one LINE 'audio' message event end-to-end.

    Args:
        event: the raw LINE event envelope (already extracted from
            ``request.json['events'][i]``).

    Side effects:
        - reply to the user via LINE Reply API
        - log a row to VoiceMessageLog
        - log a row to SymptomLog when triage runs
    """
    # Local imports to avoid circular dependencies during webhook module load
    from services.notification import (
        download_line_content,
        reply_line_message,
        send_line_push,
    )
    from services.llm import transcribe_audio
    from services.nlp import analyze_free_text, format_triage_message
    from database.voice_logs import save_voice_message

    source = event.get("source") or {}
    user_id = source.get("userId") or "unknown"
    reply_token = event.get("replyToken") or ""
    msg = event.get("message") or {}
    message_id = msg.get("id") or ""
    duration_ms = int(msg.get("duration") or 0)
    duration_sec = duration_ms // 1000 if duration_ms else 0

    masked = _short_user(user_id)
    incr("voice.event_received")
    logger.info(
        "voice event user=%s message_id=%s duration=%ss",
        masked, message_id, duration_sec,
    )

    if not message_id:
        if reply_token:
            reply_line_message(reply_token, "ไม่พบรหัสข้อความเสียง กรุณาลองใหม่อีกครั้ง")
        return

    # 1. Download
    audio_bytes = download_line_content(message_id)
    if not audio_bytes:
        incr("voice.download_fail")
        logger.warning("voice download failed user=%s message_id=%s", masked, message_id)
        if reply_token:
            reply_line_message(reply_token, "ไม่สามารถดาวน์โหลดเสียงได้ กรุณาลองส่งใหม่อีกครั้งค่ะ")
        save_voice_message(
            user_id=user_id, duration_sec=duration_sec, mime_type="audio/mp4",
            transcription_length=0, status="download_fail",
        )
        return

    # LINE delivers voice as m4a (audio/mp4 container, AAC codec). Gemini
    # accepts this directly via the audio/mp4 mime hint.
    mime_type = "audio/mp4"

    # 2. Transcribe
    transcription = transcribe_audio(audio_bytes, mime_type=mime_type)
    if transcription is None:
        incr("voice.transcribe_fail")
        if reply_token:
            reply_line_message(
                reply_token,
                "ขอโทษค่ะ ระบบถอดความเสียงขัดข้องชั่วคราว "
                "กรุณาพิมพ์ข้อความหรือส่งเสียงใหม่อีกครั้ง",
            )
        save_voice_message(
            user_id=user_id, duration_sec=duration_sec, mime_type=mime_type,
            transcription_length=0, status="transcribe_fail",
        )
        return

    if not transcription.strip() or transcription.strip() == _UNINTELLIGIBLE_MARKER:
        status = "empty" if not transcription.strip() else "unintelligible"
        incr(f"voice.{status}")
        if reply_token:
            reply_line_message(
                reply_token,
                "ขอโทษค่ะ ฟังเสียงไม่ชัดเจน กรุณาพูดใหม่อีกครั้งหรือพิมพ์ข้อความค่ะ",
            )
        save_voice_message(
            user_id=user_id, duration_sec=duration_sec, mime_type=mime_type,
            transcription_length=0, status=status,
        )
        return

    incr("voice.transcribe_ok")
    logger.info("voice transcribed user=%s chars=%d", masked, len(transcription))

    # 3. Triage through the same pipeline as typed free text
    try:
        triage = analyze_free_text(transcription) or {}
    except Exception:
        logger.exception("voice triage failed user=%s", masked)
        triage = {"risk_level": "low", "risk_score": 0, "flags": []}

    triage_msg = format_triage_message(triage)

    # 4. Reply: echo back what we heard so the patient can confirm,
    #    then deliver the triage advice.
    preview = transcription[:_REPLY_TRANSCRIPT_PREVIEW]
    if len(transcription) > _REPLY_TRANSCRIPT_PREVIEW:
        preview += "..."
    reply_text = (
        f"🎙️ ได้ยินว่า:\n\"{preview}\"\n\n"
        f"{triage_msg}"
    )
    if reply_token:
        reply_line_message(reply_token, reply_text)

    # 5. High-risk → push nurse alert (mirrors text path in nlp.py callers)
    risk = (triage.get("risk_level") or "").lower()
    if risk == "high":
        incr("voice.high_risk_alert")
        try:
            send_line_push(
                target_user_id=None,  # default = NURSE_GROUP_ID
                message=(
                    f"⚠️ Voice triage: HIGH risk\n"
                    f"User: {masked}\n"
                    f"\"{preview}\"\n"
                    f"Score: {triage.get('risk_score', 0)}"
                ),
            )
        except Exception:
            logger.exception("Failed to push voice high-risk alert")

    # 6. Audit
    save_voice_message(
        user_id=user_id, duration_sec=duration_sec, mime_type=mime_type,
        transcription_length=len(transcription), status="ok",
    )


def is_audio_event(event: Dict[str, Any]) -> bool:
    """True if ``event`` is a LINE message event carrying an audio payload."""
    if (event or {}).get("type") != "message":
        return False
    msg = event.get("message") or {}
    return msg.get("type") == "audio"
