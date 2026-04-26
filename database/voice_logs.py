# -*- coding: utf-8 -*-
"""
VoiceMessageLog persistence (Phase 5 P5-2).

Audits every voice message we receive from LINE so nurses can later see
how often patients use voice vs text and whether transcription succeeded.

**Privacy by design**: we deliberately do NOT store the audio bytes or
the full transcription content. Only metadata: who spoke, when, how
long, MIME type, transcription length, and an outcome status. Content
flows through the existing SymptomLog (after triage) just like text
messages, with the same PII scrubbing.

Schema:
  Timestamp | User_ID | Duration_Sec | MIME | Transcription_Length | Status

Status values:
  - ``ok``           → transcription succeeded and was triaged
  - ``empty``        → audio downloaded but Gemini returned empty text
  - ``unintelligible`` → model returned the explicit fallback marker
  - ``download_fail`` → LINE Content API returned no bytes
  - ``transcribe_fail`` → Gemini call failed / quota / circuit
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from config import LOCAL_TZ, SHEET_VOICE_LOG, get_logger
from database.sheets import get_spreadsheet, get_worksheet

logger = get_logger(__name__)

_HEADER = ["Timestamp", "User_ID", "Duration_Sec", "MIME",
           "Transcription_Length", "Status"]


def _get_or_create_sheet():
    """Return VoiceMessageLog worksheet, auto-creating with header on first use."""
    sheet = get_worksheet(SHEET_VOICE_LOG)
    if sheet is not None:
        return sheet

    spreadsheet = get_spreadsheet()
    if spreadsheet is None:
        logger.warning("voice_logs: no spreadsheet handle; skip auto-create")
        return None

    try:
        sheet = spreadsheet.add_worksheet(
            title=SHEET_VOICE_LOG, rows=1000, cols=len(_HEADER),
        )
        sheet.append_row(_HEADER, value_input_option="USER_ENTERED")
        logger.info("voice_logs: auto-created sheet '%s'", SHEET_VOICE_LOG)
        return sheet
    except Exception:
        logger.exception("voice_logs: failed to auto-create sheet")
        return None


def save_voice_message(
    *,
    user_id: str,
    duration_sec: Optional[int],
    mime_type: str,
    transcription_length: int,
    status: str,
) -> bool:
    """
    Append one row to VoiceMessageLog. Never raises — returns False on
    any error so the caller can keep replying to the user.

    All arguments are keyword-only to make call sites self-documenting
    (we have 5 fields with similar types and easy-to-confuse positions).
    """
    if not user_id:
        return False

    try:
        sheet = _get_or_create_sheet()
        if sheet is None:
            return False

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id,
            str(int(duration_sec)) if duration_sec else "0",
            (mime_type or "")[:50],
            str(int(transcription_length or 0)),
            (status or "")[:30],
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(
            "voice_logs: appended user=%s status=%s len=%d duration=%ss mime=%s",
            user_id, status, transcription_length, duration_sec or 0, mime_type,
        )
        return True

    except Exception:
        logger.exception("voice_logs: failed to append row user_id=%s", user_id)
        return False
