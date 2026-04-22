# -*- coding: utf-8 -*-
"""
Free-text Symptom Triage (Phase 2-E)

Analyzes an open-ended patient message (e.g. "วันนี้แผลบวมแดง ปวด 7/10 มีไข้
นิดหน่อย") and returns a structured triage result the webhook can act on.

Strategy:
1. Try LLM (Gemini) via services.llm for richer extraction.
2. Always cross-check with rule-based keyword matcher; if LLM is missing
   or fails, rule-based result is returned so the flow stays predictable.
3. The union of flags drives the final risk level so we never *lower* risk
   below what rule-based found — LLM can only escalate.
"""
from config import get_logger
from services import llm as llm_module

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Rule-based keyword matcher (fallback + safety net)
# ---------------------------------------------------------------------------
_RULES = [
    # (flag, risk_weight, keywords)
    ("wound_pus",      3, ["หนอง", "มีกลิ่น", "แฉะ", "pus", "discharge"]),
    ("wound_inflamed", 2, ["บวมแดง", "อักเสบ", "แดงร้อน", "บวม", "แดง"]),
    ("fever",          2, ["ไข้", "ตัวร้อน", "fever", "หนาวสั่น"]),
    ("severe_pain",    3, ["ปวดมาก", "ปวดรุนแรง", "ทนไม่ไหว", "10/10", "9/10", "8/10"]),
    ("moderate_pain",  1, ["ปวดปานกลาง", "7/10", "6/10"]),
    ("neuro",          3, ["ชา", "อ่อนแรง", "ขยับไม่ได้", "numb", "weakness"]),
    ("bleeding",       3, ["เลือดออก", "เลือดไหล", "bleeding"]),
    ("breathing",      3, ["หายใจลำบาก", "เหนื่อย", "หอบ", "shortness of breath"]),
    ("bedridden",      1, ["เดินไม่ได้", "ติดเตียง", "ขยับไม่ได้"]),
]


def _rule_based_analyze(text):
    t = (text or "").lower()
    score = 0
    flags = []
    for flag, weight, keywords in _RULES:
        if any(k in t for k in keywords):
            flags.append(flag)
            score += weight
    if score >= 5:
        level = "high"
    elif score >= 2:
        level = "medium"
    else:
        level = "low"
    return {
        "risk_level": level,
        "risk_score": score,
        "flags": flags,
        "summary": "",
        "source": "rule",
    }


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยพยาบาลไทยทำ triage อาการเบื้องต้นจากข้อความคนไข้หลังผ่าตัด. "
    "ตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น. รูปแบบ:\n"
    "{\n"
    '  "risk_level": "low" | "medium" | "high",\n'
    '  "flags": [string, ...],   // จาก set: wound_pus, wound_inflamed, fever, '
    "severe_pain, moderate_pain, neuro, bleeding, breathing, bedridden, other\n"
    '  "summary": "สรุปอาการสั้น ๆ ภาษาไทย 1-2 ประโยค"\n'
    "}\n"
    "ถ้าข้อมูลไม่พอให้ตั้ง risk_level=low และ flags=[]. "
    "ให้ risk_level=high ทันทีถ้าพบ: แผลมีหนอง, ไข้สูง, ปวดรุนแรง, ชา/อ่อนแรง, "
    "เลือดออกมาก, หายใจลำบาก."
)


def _llm_analyze(text):
    """Return dict from LLM or None on any failure."""
    if not llm_module.is_enabled():
        return None
    user_prompt = f"ข้อความจากคนไข้: {text}"
    data = llm_module.complete_json(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(data, dict):
        return None

    level = str(data.get("risk_level", "")).lower()
    if level not in ("low", "medium", "high"):
        level = "low"
    flags = data.get("flags") or []
    if not isinstance(flags, list):
        flags = []
    flags = [str(f) for f in flags if isinstance(f, (str, int))]
    summary = str(data.get("summary", "") or "").strip()[:400]

    return {
        "risk_level": level,
        "risk_score": None,  # LLM doesn't need to produce numeric score
        "flags": flags,
        "summary": summary,
        "source": "llm",
    }


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------
_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


def _max_level(a, b):
    if _LEVEL_ORDER.get(a, 0) >= _LEVEL_ORDER.get(b, 0):
        return a
    return b


def analyze_free_text(text):
    """
    Run rule-based and (if available) LLM triage, merge, return single dict.

    Output contract:
        {
          "risk_level": "low" | "medium" | "high",
          "risk_score": int | None,
          "flags": [str, ...],
          "summary": str,
          "source": "rule" | "llm" | "merged"
        }
    """
    if not text or not str(text).strip():
        return {
            "risk_level": "low",
            "risk_score": 0,
            "flags": [],
            "summary": "",
            "source": "rule",
        }

    rule = _rule_based_analyze(text)
    llm_res = _llm_analyze(text)

    if llm_res is None:
        return rule

    merged_flags = list(dict.fromkeys(rule["flags"] + llm_res["flags"]))
    merged_level = _max_level(rule["risk_level"], llm_res["risk_level"])
    summary = llm_res.get("summary") or rule.get("summary") or ""

    return {
        "risk_level": merged_level,
        "risk_score": rule["risk_score"],
        "flags": merged_flags,
        "summary": summary,
        "source": "merged",
    }


# ---------------------------------------------------------------------------
# Message builder for webhook response
# ---------------------------------------------------------------------------
def format_triage_message(result):
    """Build a patient-facing Thai message from an analyze_free_text() dict."""
    level = result.get("risk_level", "low")
    flags = result.get("flags") or []
    summary = result.get("summary") or ""

    if level == "high":
        header = "🚨 อาการเข้าเกณฑ์เสี่ยงสูง - ต้องปรึกษาพยาบาลทันทีค่ะ"
    elif level == "medium":
        header = "⚠️ อาการควรเฝ้าระวัง แนะนำปรึกษาพยาบาลค่ะ"
    else:
        header = "🟢 อาการยังไม่มีสัญญาณเสี่ยงชัดเจนค่ะ"

    parts = [header]
    if summary:
        parts.append(f"\n📋 สรุปอาการ: {summary}")
    if flags:
        pretty = ", ".join(flags)
        parts.append(f"🔎 ที่พบ: {pretty}")

    if level == "high":
        parts.append("\n💡 พิมพ์ 'ปรึกษาพยาบาล' เพื่อเข้าคิว หรือ 1669 หากฉุกเฉิน")
    elif level == "medium":
        parts.append("\n💡 หากอาการแย่ลง พิมพ์ 'ปรึกษาพยาบาล' ได้เลยนะคะ")
    else:
        parts.append("\n💡 หากไม่แน่ใจ พิมพ์ 'ปรึกษาพยาบาล' ได้ค่ะ")

    return "\n".join(parts)
