# -*- coding: utf-8 -*-
"""
Pre-Consult Briefing (Phase 2-B)

Produces a short, nurse-facing clinical briefing from a teleconsult
session's description so the nurse can prep before picking up the call.

Strategy:
1. Run the rule-based free-text triage (`services.nlp.analyze_free_text`)
   on the patient's description to extract flags + risk.
2. If LLM is enabled, ask it to produce a one-paragraph clinical summary
   plus 2-3 focused questions for the nurse. If not, fall back to a
   templated briefing derived from the triage result + issue category.

This module never raises: callers use the returned string as-is.
"""
from config import get_logger, ISSUE_CATEGORIES
from services import llm as llm_module
from services.nlp import analyze_free_text

logger = get_logger(__name__)


_SYSTEM_PROMPT = (
    "คุณคือพยาบาลอาวุโส สรุปรายละเอียดคนไข้ให้พยาบาลที่จะโทรกลับ. "
    "ตอบเป็น JSON เท่านั้น:\n"
    "{\n"
    '  "summary": "สรุปอาการสั้น ๆ 1-2 ประโยคภาษาไทย",\n'
    '  "questions": ["คำถามที่ควรถาม", ...]   // 2-3 ข้อ\n'
    "}\n"
    "ห้ามใช้ข้อมูลระบุตัวตนใด ๆ ในคำตอบ."
)


def _llm_briefing(issue_type, description, triage):
    if not llm_module.is_enabled():
        return None
    cat = ISSUE_CATEGORIES.get(issue_type, {})
    user_prompt = (
        f"ประเภทการปรึกษา: {cat.get('name_th', issue_type)}\n"
        f"คำอธิบายจากคนไข้: {description or '(ไม่ระบุ)'}\n"
        f"Triage (rule-based): risk={triage.get('risk_level')}, "
        f"flags={triage.get('flags')}"
    )
    data = llm_module.complete_json(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary") or "").strip()
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        questions = []
    questions = [str(q).strip() for q in questions if str(q).strip()][:3]
    if not summary and not questions:
        return None
    return {"summary": summary, "questions": questions}


def _fallback_briefing(issue_type, description, triage):
    """Rule-based briefing used when LLM is unavailable or fails."""
    cat = ISSUE_CATEGORIES.get(issue_type, {})
    category_name = cat.get("name_th", issue_type or "อื่น ๆ")

    flags = triage.get("flags") or []
    summary_bits = [f"หมวด: {category_name}"]
    if description:
        short = description.strip()
        if len(short) > 140:
            short = short[:140] + "..."
        summary_bits.append(f"คำอธิบาย: {short}")
    if flags:
        summary_bits.append("สัญญาณที่พบ: " + ", ".join(flags))

    questions = []
    flag_set = set(flags)
    if "wound_pus" in flag_set or "wound_inflamed" in flag_set:
        questions.append("ลักษณะ/ปริมาณสิ่งคัดหลั่งจากแผลเป็นอย่างไร?")
    if "fever" in flag_set:
        questions.append("อุณหภูมิสูงสุดในวันนี้กี่องศา? ทานยาลดไข้หรือยัง?")
    if "severe_pain" in flag_set or "moderate_pain" in flag_set:
        questions.append("ระดับปวดตอนนี้กี่คะแนน (0-10)? ทานยาแก้ปวดครั้งล่าสุดเมื่อไหร่?")
    if "neuro" in flag_set:
        questions.append("อาการชา/อ่อนแรง: จุดไหน ข้างไหน เริ่มเมื่อไหร่?")
    if "breathing" in flag_set:
        questions.append("หอบเหนื่อยขณะพักหรือไม่? มีประวัติโรคหัวใจ/ปอดหรือไม่?")
    if not questions:
        questions.append("อาการเริ่มตั้งแต่เมื่อไหร่? แย่ลงหรือคงที่?")
        questions.append("มียาที่ทานอยู่หรือแพ้ยาอะไรไหม?")

    return {
        "summary": "\n".join(summary_bits),
        "questions": questions[:3],
    }


def build_pre_consult_briefing(user_id, issue_type, description=""):
    """
    Return a formatted Thai briefing string ready to append to the nurse
    notification. Never raises.
    """
    try:
        triage = analyze_free_text(description or "")
        briefing = _llm_briefing(issue_type, description, triage)
        if briefing is None:
            briefing = _fallback_briefing(issue_type, description, triage)

        risk = triage.get("risk_level", "low")
        risk_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(risk, "🟢")

        parts = [
            "\n━━━━━━━━━━━━━━━━━━",
            "📝 Pre-consult briefing",
            f"{risk_emoji} Risk: {risk}",
        ]
        if briefing.get("summary"):
            parts.append(briefing["summary"])
        questions = briefing.get("questions") or []
        if questions:
            parts.append("คำถามที่ควรถาม:")
            for i, q in enumerate(questions, 1):
                parts.append(f"  {i}. {q}")
        return "\n".join(parts)
    except Exception:
        logger.exception("Failed to build pre-consult briefing")
        return ""
