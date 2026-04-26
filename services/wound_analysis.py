# -*- coding: utf-8 -*-
"""
Wound Image Analysis (Sprint 2 S2-2).

Pure-function orchestrator: takes raw image bytes from LINE Content API,
sends to Gemini Vision via ``services.llm.complete_image_json``, and returns
a normalized dict that the route layer can persist + alert on.

Design notes:
- This module never makes LINE API calls and never writes to Sheets. It is
  meant to be cheap to test (mock ``llm.complete_image_json`` and you are
  done) and reusable from any future entry point (admin UI, batch job).
- All clinical caveats live in the system prompt — we instruct the model
  not to diagnose or prescribe, and to surface uncertainty.
- Returns None on any failure (LLM disabled, network error, malformed JSON,
  image too large). The caller is responsible for the user-facing fallback
  message.
"""
from __future__ import annotations

from typing import Any, Optional

from config import LLM_VISION_MAX_IMAGE_BYTES, get_logger
from services import llm as llm_module
from services.metrics import incr as _metric

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Prompt — kept in module scope so tests can verify it stays in Thai and
# does not contain disallowed clinical wording.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยพยาบาลที่ประเมินภาพแผลผ่าตัดเบื้องต้น ก่อนพยาบาลตรวจซ้ำ.\n"
    "ตอบเป็น JSON เท่านั้นในรูปแบบนี้:\n"
    "{\n"
    '  "severity": "low" หรือ "medium" หรือ "high",\n'
    '  "observations": ["สิ่งที่เห็นในภาพ 2-4 ข้อ ภาษาไทย"],\n'
    '  "advice": "คำแนะนำ 1-2 ประโยคสำหรับผู้ป่วย",\n'
    '  "confidence": ตัวเลข 0.0-1.0\n'
    "}\n"
    "เกณฑ์ความรุนแรง:\n"
    "- high: เห็นหนอง, รอยแยก, สีดำคล้ำ, เลือดออกใหม่, บวมแดงรุนแรง, แผลเปิด\n"
    "- medium: บวมหรือแดงปานกลาง, มีน้ำเหลือง, ขอบแผลซีด\n"
    "- low: แห้ง สะอาด ไม่มีอาการอักเสบที่ชัดเจน\n"
    "ข้อบังคับ:\n"
    "- ห้ามวินิจฉัยโรคและห้ามสั่งจ่ายยา\n"
    "- ถ้าไม่แน่ใจให้ตั้ง confidence ต่ำและ severity เป็น medium\n"
    "- ถ้าภาพไม่ใช่แผลผ่าตัด/ผิวหนัง ตอบ severity=low, observations=['ไม่ใช่ภาพแผล'], confidence=0.0\n"
    "- คำแนะนำต้องลงท้ายด้วย 'หากอาการแย่ลงให้ติดต่อพยาบาล'"
)

USER_PROMPT = "ประเมินภาพแผลนี้และตอบตามรูปแบบ JSON ที่กำหนด"

_VALID_SEVERITIES = {"low", "medium", "high"}


def _normalize(raw: Any) -> Optional[dict[str, Any]]:
    """
    Validate and clamp model output. Returns None if structure is wrong.
    Defensive against partial / hallucinated responses.
    """
    if not isinstance(raw, dict):
        return None

    severity = str(raw.get("severity") or "").strip().lower()
    if severity not in _VALID_SEVERITIES:
        # Don't reject — fall back to medium so nurses still get a notification
        # for review. This is safer than dropping the analysis silently.
        logger.warning("wound_analysis: invalid severity=%r → coerced to medium", severity)
        severity = "medium"

    observations_raw = raw.get("observations") or []
    if not isinstance(observations_raw, list):
        observations_raw = []
    observations = [str(o).strip()[:200] for o in observations_raw if str(o).strip()][:5]

    advice = str(raw.get("advice") or "").strip()[:500]

    try:
        confidence = float(raw.get("confidence") or 0.5)
    except (ValueError, TypeError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return {
        "severity": severity,
        "observations": observations,
        "advice": advice,
        "confidence": round(confidence, 2),
    }


def analyze_wound_image(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> Optional[dict[str, Any]]:
    """
    Analyze a single wound image.

    Args:
        image_bytes: Raw bytes (jpeg/png) downloaded from LINE Content API.
        mime_type: MIME type — LINE delivers jpeg by default.

    Returns:
        dict with keys ``severity`` (low/medium/high), ``observations`` (list
        of short strings, ≤5), ``advice`` (≤500 chars), ``confidence`` (0-1).
        Returns ``None`` if:
        - LLM provider not configured (rule-based fallback at caller)
        - Image is empty / oversized (over LLM_VISION_MAX_IMAGE_BYTES)
        - Network / parse error
        - Vision quota / circuit breaker tripped
    """
    if not image_bytes:
        _metric("wound_analysis.skip_empty_image")
        return None

    size = len(image_bytes)
    if size > LLM_VISION_MAX_IMAGE_BYTES:
        logger.warning(
            "wound_analysis: image too large (%d bytes > cap %d) — rejecting",
            size, LLM_VISION_MAX_IMAGE_BYTES,
        )
        _metric("wound_analysis.skip_oversize")
        return None

    if not llm_module.is_enabled():
        # No provider — caller will use the rule-based fallback message.
        _metric("wound_analysis.skip_llm_disabled")
        return None

    raw = llm_module.complete_image_json(
        system=SYSTEM_PROMPT,
        user_text=USER_PROMPT,
        image_bytes=image_bytes,
        mime_type=mime_type or "image/jpeg",
    )
    if raw is None:
        # Already logged + metric'd in llm module
        _metric("wound_analysis.llm_returned_none")
        return None

    result = _normalize(raw)
    if result is None:
        _metric("wound_analysis.normalize_failed")
        return None

    _metric(f"wound_analysis.severity.{result['severity']}")
    logger.info(
        "wound_analysis ok severity=%s confidence=%.2f obs_count=%d image_kb=%d",
        result["severity"], result["confidence"], len(result["observations"]),
        size // 1024,
    )
    return result
