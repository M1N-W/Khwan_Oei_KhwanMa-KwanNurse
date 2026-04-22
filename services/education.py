# -*- coding: utf-8 -*-
"""
Personalized Education Recommender (Phase 2-C)

Recommends which existing knowledge guides to show a patient given their
profile (age, sex, surgery_type, diseases). The underlying guides
themselves live in `services.knowledge` and are unchanged; this module
only ranks and explains WHY each guide applies.

Strategy:
1. Rule-based ranking gives a deterministic baseline so the bot works with
   no LLM configured.
2. If LLM is enabled, ask it to refine the order and produce short
   Thai-language reasons tailored to the patient.
"""
from config import get_logger
from services import llm as llm_module

logger = get_logger(__name__)

# Canonical guide catalog. Key must match the topic keys understood by
# routes/webhook.py::handle_get_knowledge so downstream wiring stays simple.
GUIDE_CATALOG = [
    {
        "key": "wound_care",
        "title": "การดูแลแผล",
        "default_reason": "หลังผ่าตัดทุกรายควรทราบวิธีดูแลแผลและสังเกตการติดเชื้อ",
    },
    {
        "key": "physical_therapy",
        "title": "กายภาพบำบัด/การบริหารข้อ",
        "default_reason": "ช่วยฟื้นฟูการเคลื่อนไหวและลดภาวะข้อติด",
    },
    {
        "key": "dvt_prevention",
        "title": "ป้องกันลิ่มเลือดอุดตัน",
        "default_reason": "ผู้ป่วยหลังผ่าตัดใหญ่มีความเสี่ยง DVT โดยเฉพาะช่วงเคลื่อนไหวน้อย",
    },
    {
        "key": "medication",
        "title": "การรับประทานยา",
        "default_reason": "ช่วยให้ใช้ยาครบถ้วนและหลีกเลี่ยงผลข้างเคียง",
    },
    {
        "key": "warning_signs",
        "title": "สัญญาณอันตรายที่ต้องพบแพทย์",
        "default_reason": "ช่วยให้ผู้ป่วยและญาติรู้ทันอาการที่ต้องรีบมา รพ.",
    },
]

_ALL_KEYS = [g["key"] for g in GUIDE_CATALOG]
_CATALOG_BY_KEY = {g["key"]: g for g in GUIDE_CATALOG}


def _normalize_profile(profile):
    """Make profile robust against missing fields / Thai values."""
    profile = dict(profile or {})
    diseases_raw = profile.get("diseases") or profile.get("disease") or []
    if isinstance(diseases_raw, str):
        diseases = [d.strip() for d in diseases_raw.replace(",", " ").split() if d.strip()]
    else:
        diseases = [str(d) for d in diseases_raw]
    profile["diseases"] = diseases

    try:
        profile["age"] = int(profile.get("age")) if profile.get("age") not in (None, "") else None
    except (TypeError, ValueError):
        profile["age"] = None

    profile["sex"] = str(profile.get("sex") or "").lower()
    profile["surgery_type"] = str(profile.get("surgery_type") or "").lower()
    return profile


# ---------------------------------------------------------------------------
# Rule-based ranking
# ---------------------------------------------------------------------------
def _rule_based_rank(profile):
    profile = _normalize_profile(profile)
    age = profile.get("age")
    surgery = profile.get("surgery_type", "")
    diseases = profile.get("diseases") or []
    diseases_lower = [d.lower() for d in diseases]

    scores = {k: 1 for k in _ALL_KEYS}  # base score: everything is at least worth showing

    # Wound care is always most important right after surgery; keep it at
    # the top regardless of other boosts.
    scores["wound_care"] += 10

    # Warning signs too.
    scores["warning_signs"] += 3

    # Orthopedic / major surgery → physical therapy + DVT emphasis.
    ortho_markers = ("hip", "knee", "ortho", "กระดูก", "ข้อ", "สะโพก", "เข่า")
    if any(m in surgery for m in ortho_markers):
        scores["physical_therapy"] += 4
        scores["dvt_prevention"] += 3

    # Elderly → DVT + medication + warning signs.
    if age is not None and age >= 60:
        scores["dvt_prevention"] += 2
        scores["medication"] += 2
        scores["warning_signs"] += 1

    # Cardiovascular / diabetes / kidney patients usually on multiple meds.
    if any(d in ("หัวใจ", "ความดัน", "เบาหวาน", "ไต") for d in diseases_lower):
        scores["medication"] += 3
        scores["dvt_prevention"] += 1

    ordered_keys = sorted(_ALL_KEYS, key=lambda k: -scores[k])
    return [
        {
            "key": k,
            "title": _CATALOG_BY_KEY[k]["title"],
            "reason": _CATALOG_BY_KEY[k]["default_reason"],
            "source": "rule",
        }
        for k in ordered_keys
    ]


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "คุณคือพยาบาลไทยที่แนะนำหัวข้อความรู้หลังผ่าตัดให้เหมาะกับผู้ป่วยแต่ละราย. "
    "ตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น. รูปแบบ:\n"
    "{\n"
    '  "ranked": [\n'
    '    {"key": "<guide_key>", "reason": "<เหตุผลสั้น ๆ 1 ประโยคภาษาไทย>"},\n'
    "    ...\n"
    "  ]\n"
    "}\n"
    "ใช้เฉพาะ key เหล่านี้: wound_care, physical_therapy, dvt_prevention, "
    "medication, warning_signs. รวมต้องมีครบทุก key ไม่ซ้ำ. "
    "เรียงจากสำคัญที่สุดก่อน."
)


def _llm_refine(profile, rule_result):
    if not llm_module.is_enabled():
        return None

    profile = _normalize_profile(profile)
    user_prompt = (
        f"โปรไฟล์ผู้ป่วย:\n"
        f"- อายุ: {profile.get('age')}\n"
        f"- เพศ: {profile.get('sex')}\n"
        f"- ประเภทผ่าตัด: {profile.get('surgery_type')}\n"
        f"- โรคประจำตัว: {', '.join(profile.get('diseases') or []) or 'ไม่มี'}\n"
        f"ลำดับเบื้องต้น (rule-based): "
        f"{', '.join(r['key'] for r in rule_result)}"
    )
    data = llm_module.complete_json(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(data, dict):
        return None
    ranked = data.get("ranked")
    if not isinstance(ranked, list):
        return None

    seen = set()
    refined = []
    for item in ranked:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in _CATALOG_BY_KEY or key in seen:
            continue
        seen.add(key)
        reason = str(item.get("reason") or _CATALOG_BY_KEY[key]["default_reason"]).strip()[:200]
        refined.append({
            "key": key,
            "title": _CATALOG_BY_KEY[key]["title"],
            "reason": reason,
            "source": "llm",
        })

    # Ensure all catalog keys are present (append any missing in rule order).
    for r in rule_result:
        if r["key"] not in seen:
            refined.append(r)

    return refined if refined else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def recommend_guides(profile, top_n=3):
    """
    Return ordered recommendations for the given patient profile.

    Args:
        profile: dict with any subset of {age, sex, surgery_type, diseases}
        top_n: truncate result to this many items (0/None = all)

    Returns:
        list of dicts: [{key, title, reason, source}, ...]
    """
    rule_result = _rule_based_rank(profile)
    refined = _llm_refine(profile, rule_result)
    result = refined if refined else rule_result
    if top_n:
        return result[:top_n]
    return result


def format_recommendations_message(recommendations):
    """Build a short Thai message listing recommendations."""
    if not recommendations:
        return ""
    lines = ["🎯 ความรู้ที่แนะนำสำหรับคุณ:"]
    for i, item in enumerate(recommendations, 1):
        lines.append(f"{i}. {item['title']}")
        if item.get("reason"):
            lines.append(f"   └ {item['reason']}")
    lines.append("\n💡 พิมพ์ชื่อหัวข้อที่ต้องการอ่าน เช่น 'ดูแลแผล'")
    return "\n".join(lines)
