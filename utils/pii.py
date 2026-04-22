# -*- coding: utf-8 -*-
"""
PII Scrubber
Strip personally-identifiable information from text BEFORE it leaves the
bot's trust boundary (e.g. before sending to an external LLM provider).

Kept conservative: we would rather over-mask than leak. The scrubber does
NOT try to be clever about medical context; it only targets well-known
high-risk patterns for the Thai healthcare / LINE OA setting.
"""
import re

# LINE user ids look like "Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" (33 chars, 'U'
# followed by 32 lowercase hex). Group ids start with 'C', room ids 'R'.
_LINE_ID_RE = re.compile(r"\b[UCR][0-9a-f]{32}\b")

# Thai mobile: 10 digits starting with 0 then 6/8/9; also allow spaces/dashes.
_THAI_PHONE_RE = re.compile(r"\b0[689][-\s]?\d{1,4}[-\s]?\d{3,4}[-\s]?\d{0,4}\b")

# Thai national id: 13 consecutive digits (optionally dash-separated 1-4-5-2-1)
_THAI_NID_RE = re.compile(
    r"\b\d{1}[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d{1}\b|\b\d{13}\b"
)

# Basic email.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Session ids emitted by this bot (TCyyyymmddHHMMSSxxxxxxxx).
_SESSION_ID_RE = re.compile(r"\bTC\d{14}[0-9a-f]{8}\b")


def scrub_pii(text):
    """
    Return text with high-risk PII replaced by placeholders.

    Safe to pass any string including None. Returns the same string (minus
    PII) so callers can log or send to LLM without leaking identifiers.
    """
    if not text:
        return text
    if not isinstance(text, str):
        text = str(text)

    redacted = text
    redacted = _LINE_ID_RE.sub("[LINE_ID]", redacted)
    redacted = _SESSION_ID_RE.sub("[SESSION_ID]", redacted)
    redacted = _EMAIL_RE.sub("[EMAIL]", redacted)
    redacted = _THAI_NID_RE.sub("[NATIONAL_ID]", redacted)
    redacted = _THAI_PHONE_RE.sub("[PHONE]", redacted)
    return redacted


def scrub_user_id(user_id):
    """
    Short preview form of a user id for logs/messages that still need
    identity continuity without full disclosure.
    """
    if not user_id:
        return "[unknown]"
    s = str(user_id)
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}***{s[-4:]}"
