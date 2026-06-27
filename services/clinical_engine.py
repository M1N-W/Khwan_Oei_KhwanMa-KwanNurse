# -*- coding: utf-8 -*-
"""
Clinical Risk Engine Service Module (KWN-10)
Pure, side-effect-free decision logic for patient symptom and personal risk evaluation.
"""
from dataclasses import dataclass
from typing import Optional, List
import json
from config import (
    get_logger,
    RISK_DISEASES,
    DISEASE_MAPPING,
    DISEASE_NEGATIVES
)

logger = get_logger(__name__)

# Pre-sorted disease keys (longest first)
_SORTED_DISEASE_KEYS = sorted(DISEASE_MAPPING.keys(), key=lambda x: -len(x))


@dataclass(frozen=True)
class SymptomClinicalInput:
    pain: Optional[int]
    wound: Optional[str]
    fever: Optional[str]
    mobility: Optional[str]
    neuro: Optional[str] = None


@dataclass(frozen=True)
class SymptomClinicalOutput:
    risk_score: int
    risk_code: str
    risk_label: str
    risk_details: List[str]
    action_advice: str
    patient_message: str
    notification_required: bool


@dataclass(frozen=True)
class PersonalClinicalInput:
    age: Optional[int]
    weight: Optional[float]
    height: Optional[float]
    disease: Optional[str]


@dataclass(frozen=True)
class PersonalClinicalOutput:
    risk_score: int
    risk_level: str
    risk_factors: List[str]
    bmi: float
    diseases_normalized: List[str]
    description: str
    advice: List[str]
    patient_message: str
    notification_required: bool


def evaluate_symptom_risk(inputs: SymptomClinicalInput) -> SymptomClinicalOutput:
    """
    Pure logic to calculate symptom-based risk score and messages.
    No I/O or side-effects.
    """
    from services.risk_levels import risk_level_from_score
    risk_score = 0
    risk_details = []
    
    # Pain Score Analysis
    try:
        p_val = int(inputs.pain) if inputs.pain is not None and str(inputs.pain).strip() != "" else 0
    except (ValueError, TypeError):
        p_val = 0
    
    if p_val >= 8:
        risk_score += 3
        risk_details.append(f"🔴 ความปวดระดับสูง ({p_val}/10)")
    elif p_val >= 6:
        risk_score += 1
        risk_details.append(f"🟡 ความปวดปานกลาง ({p_val}/10)")
    elif p_val > 0:
        risk_details.append(f"🟢 ความปวดเล็กน้อย ({p_val}/10)")
    
    # Wound Status Analysis
    wound_text = str(inputs.wound or "").lower()
    if any(x in wound_text for x in ["หนอง", "มีกลิ่น", "แฉะ", "pus", "discharge"]):
        risk_score += 3
        risk_details.append("🔴 แผลมีหนองหรือมีกลิ่น - ต้องพบแพทย์ทันที!")
    elif any(x in wound_text for x in ["บวมแดง", "อักเสบ", "swelling", "red", "inflamed"]):
        risk_score += 2
        risk_details.append("🟡 แผลบวมแดงอักเสบ")
    elif any(x in wound_text for x in ["ปกติ", "ดี", "แห้ง", "normal", "dry", "good"]):
        risk_details.append("🟢 สภาพแผลปกติ")
    
    # Fever Check
    fever_text = str(inputs.fever or "").strip().lower()
    is_no_fever = (
        fever_text in ("", "ไม่", "no")
        or any(neg in fever_text for neg in [
            "ไม่มี", "ไม่ไข้", "ไม่มีไข้", "ไม่ร้อน", "ปกติ", "normal", "no fever"
        ])
    )
    has_fever = (not is_no_fever) and any(x in fever_text for x in [
        "มี", "ตัวร้อน", "fever", "hot", "ไข้", "ร้อน"
    ])
    if has_fever:
        risk_score += 2
        risk_details.append("🔴 มีไข้ - อาจมีการติดเชื้อ")
    else:
        risk_details.append("🟢 ไม่มีไข้")
    
    # Mobility Status
    # Hot-fix (KWN-10-HF): Sudden loss of mobility is a surgical emergency (DVT/Dislocation)
    # and must be escalated immediately to Critical (+3), not just Moderate (+1).
    mobility_text = str(inputs.mobility or "").lower()
    _sudden_keywords = ["กะทันหัน", "ทันที", "เพิ่งเดินไม่ได้", "suddenly", "suddenly unable", "abruptly"]
    _mobility_lost_keywords = ["ไม่ได้", "ติดเตียง", "ไม่เดิน", "cannot", "bedridden"]
    if any(k in mobility_text for k in _sudden_keywords) and any(k in mobility_text for k in _mobility_lost_keywords):
        risk_score += 3
        risk_details.append("🔴 สูญเสียการเคลื่อนไหวอย่างกะทันหัน - ต้องประเมิน DVT/ข้อหลุดทันที!")
    elif any(x in mobility_text for x in _mobility_lost_keywords):
        risk_score += 1
        risk_details.append("🟡 เคลื่อนไหวลำบาก")
    elif any(x in mobility_text for x in ["เดินได้", "ปกติ", "normal", "can walk"]):
        risk_details.append("🟢 เคลื่อนไหวได้ปกติ")

    # Neuro Symptoms
    neuro_text = str(inputs.neuro or "").lower()
    if neuro_text and neuro_text not in ("none", "no", "ไม่มี", "ไม่", "ปกติ"):
        if any(x in neuro_text for x in [
            "อ่อนแรง", "ขยับไม่ได้", "weakness", "paralysis", "อัมพาต"
        ]):
            risk_score += 3
            risk_details.append("🔴 กล้ามเนื้ออ่อนแรง - สัญญาณเส้นประสาท ต้องพบแพทย์ทันที!")
        elif any(x in neuro_text for x in ["ชา", "numb", "tingling", "เหน็บ"]):
            risk_score += 2
            risk_details.append("🟡 อาการชา - ควรปรึกษาพยาบาล")
        elif any(x in neuro_text for x in ["ปวดร้าว", "radiating", "ร้าวลงขา", "ร้าวลงแขน"]):
            risk_score += 2
            risk_details.append("🟡 ปวดร้าวตามเส้นประสาท")
    elif neuro_text in ("ไม่มี", "ไม่", "none", "no", "ปกติ"):
        risk_details.append("🟢 ไม่มีอาการทางระบบประสาท")

    risk_code = risk_level_from_score(risk_score)
    if risk_score >= 5:
        risk_label = "🚨 อันตราย - ต้องพบแพทย์ทันที!"
        emoji = "🚨"
        action = "กรุณาติดต่อพยาบาลหรือมาโรงพยาบาลทันที!"
        color = "🔴"
    elif risk_score >= 3:
        risk_label = "⚠️ เสี่ยงสูง"
        emoji = "⚠️"
        action = "กรุณากดปุ่ม 'ปรึกษาพยาบาล' หรือโทรติดต่อทันที"
        color = "🟠"
    elif risk_score >= 2:
        risk_label = "🟡 เสี่ยงปานกลาง"
        emoji = "🟡"
        action = "เฝ้าระวังอาการใกล้ชิด 24 ชม. ถ้าอาการแย่กรุณาติดต่อ"
        color = "🟡"
    elif risk_score == 1:
        risk_label = "🟢 เสี่ยงต่ำ (เฝ้าระวัง)"
        emoji = "🟢"
        action = "โดยรวมปกติดี แต่ต้องสังเกตอาการต่อไป"
        color = "🟢"
    else:
        risk_label = "✅ ปกติดี"
        emoji = "✅"
        action = "แผลหายดี ยอดเยี่ยมมาก! กรุณารายงานอาการต่อเนื่อง"
        color = "🟢"
    
    # Build message
    message = f"{emoji} ผลประเมินอาการ\n"
    message += "=" * 30 + "\n\n"
    message += "📋 รายละเอียด:\n"
    for detail in risk_details:
        message += f"  {detail}\n"
    message += f"\n{color} ระดับความเสี่ยง: {risk_label}\n"
    message += f"(คะแนนรวม: {risk_score})\n\n"
    message += f"💡 คำแนะนำ:\n{action}"

    return SymptomClinicalOutput(
        risk_score=risk_score,
        risk_code=risk_code,
        risk_label=risk_label,
        risk_details=risk_details,
        action_advice=action,
        patient_message=message,
        notification_required=(risk_score >= 3)
    )


def normalize_diseases(disease_param) -> List[str]:
    """
    Extract and normalize disease names from various formats.
    Pure logic.
    """
    if not disease_param:
        return []
    
    def extract_items(param):
        items = []
        if isinstance(param, list):
            raw = param
        else:
            raw = [param]
        
        for it in raw:
            if it is None:
                continue
            if isinstance(it, dict):
                v = (it.get('name') or it.get('value') or 
                     it.get('original') or it.get('displayName'))
                if not v:
                    try:
                        v = json.dumps(it, ensure_ascii=False)
                    except:
                        v = str(it)
            else:
                v = str(it)
            v = v.strip()
            if v:
                items.append(v)
        return items
    
    raw_items = extract_items(disease_param)
    normalized = []
    seen = set()
    
    for raw in raw_items:
        s = raw.lower().strip()
        if s in DISEASE_NEGATIVES or any(neg in s for neg in ["no disease", "ไม่มี"]):
            continue

        # Hot-fix (KWN-10-HF): Scan the ENTIRE string for ALL matching disease keywords.
        # Previous code had `break` after first match which caused Undertriage for patients
        # with multiple comorbidities written in a single freetext string (e.g. "เบาหวาน ความดัน").
        matched_any = False
        for key in _SORTED_DISEASE_KEYS:
            if key in s:
                canon = DISEASE_MAPPING[key]
                if canon not in seen:
                    normalized.append(canon)
                    seen.add(canon)
                matched_any = True
                # NOTE: No `break` — continue scanning for additional diseases in the same string.

        if not matched_any:
            candidate = raw.strip()
            if candidate and candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)

    return normalized


def evaluate_personal_risk(inputs: PersonalClinicalInput) -> PersonalClinicalOutput:
    """
    Pure logic to calculate personal demographic risk.
    No I/O or side-effects.
    """
    risk_score = 0
    risk_factors = []
    bmi = 0.0
    
    try:
        age_val = int(inputs.age) if inputs.age is not None and str(inputs.age).strip() != "" else None
    except (ValueError, TypeError):
        age_val = None
    
    try:
        weight_val = float(inputs.weight) if inputs.weight is not None and str(inputs.weight).strip() != "" else None
    except (ValueError, TypeError):
        weight_val = None
    
    try:
        height_cm = float(inputs.height) if inputs.height is not None and str(inputs.height).strip() != "" else None
    except (ValueError, TypeError):
        height_cm = None
    
    # Calculate BMI
    if height_cm and weight_val and height_cm > 0:
        height_m = height_cm / 100.0
        bmi = weight_val / (height_m ** 2)
    
    # Age Risk Factor
    # Hot-fix (KWN-10-HF): Lower high-risk threshold from ≥70 to ≥65 per Geriatric Medicine
    # guidelines (PMID 40223829 — OR 1.577 for age ≥65 in orthopedic revision surgery).
    if age_val is not None:
        if age_val >= 65:
            risk_score += 2
            risk_factors.append(f"🔴 อายุ {age_val} ปี (สูงอายุ — เสี่ยงสูงต่อภาวะแทรกซ้อน)")
        elif age_val >= 55:
            risk_score += 1
            risk_factors.append(f"🟡 อายุ {age_val} ปี (ช่วงก่อนสูงอายุ — ควรเฝ้าระวัง)")
        else:
            risk_factors.append(f"🟢 อายุ {age_val} ปี (ปกติ)")
    
    # BMI Risk Factor
    if bmi > 0:
        if bmi >= 35:
            risk_score += 2
            risk_factors.append(f"🔴 BMI {bmi:.1f} (อ้วนมาก)")
        elif bmi >= 30:
            risk_score += 1
            risk_factors.append(f"🟡 BMI {bmi:.1f} (อ้วน)")
        elif bmi < 18.5:
            risk_score += 1
            risk_factors.append(f"🟡 BMI {bmi:.1f} (ผอมเกินไป)")
        elif 18.5 <= bmi < 23:
            risk_factors.append(f"🟢 BMI {bmi:.1f} (ปกติดี)")
        elif 23 <= bmi < 25:
            risk_factors.append(f"🟢 BMI {bmi:.1f} (ค่อนข้างมาตรฐาน)")
        else:
            risk_factors.append(f"🟡 BMI {bmi:.1f} (น้ำหนักเกิน)")
    
    # Disease Risk Factors
    disease_normalized = normalize_diseases(inputs.disease)
    high_risk_diseases = [d for d in disease_normalized if d in RISK_DISEASES]
    
    if len(high_risk_diseases) >= 2:
        risk_score += 3
        risk_factors.append(f"🔴 มีโรคประจำตัวหลายโรค: {', '.join(high_risk_diseases)}")
    elif len(high_risk_diseases) == 1:
        risk_score += 2
        risk_factors.append(f"🟡 มีโรคประจำตัว: {high_risk_diseases[0]}")
    elif disease_normalized:
        risk_factors.append(f"🟡 โรคอื่นๆ: {', '.join(disease_normalized)}")
    else:
        risk_factors.append("🟢 ไม่มีโรคประจำตัว")
    
    # Risk Level Classification
    if risk_score >= 5:
        risk_level = "🔴 สูงมาก (Very High Risk)"
        emoji = "🚨"
        desc = "มีความเสี่ยงสูงมากต่อภาวะแทรกซ้อน"
        advice = [
            "• พยาบาลจะติดตามใกล้ชิดเป็นพิเศษ",
            "• รายงานอาการทุกวัน",
            "• ปฏิบัติตามคำแนะนำอย่างเคร่งครัด",
            "• หากมีอาการผิดปกติให้รีบติดต่อทันที"
        ]
    elif risk_score >= 4:
        risk_level = "🟠 สูง (High Risk)"
        emoji = "⚠️"
        desc = "มีความเสี่ยงสูงต่อภาวะแทรกซ้อน"
        advice = [
            "• พยาบาลจะติดตามใกล้ชิดเป็นพิเศษ",
            "• คุมโรคประจำตัวให้ดี",
            "• รายงานอาการสม่ำเสมอ",
            "• ระวังสัญญาณเตือน"
        ]
    elif risk_score >= 2:
        risk_level = "🟡 ปานกลาง (Moderate Risk)"
        emoji = "🟡"
        desc = "มีความเสี่ยงปานกลาง"
        advice = [
            "• คุมโรคประจำตัวและรายงานอาการสม่ำเสมอ",
            "• ดูแลสุขภาพให้ดี",
            "• ออกกำลังกายตามที่แนะนำ",
            "• รับประทานยาตรงเวลา"
        ]
    else:
        risk_level = "🟢 ต่ำ (Low Risk)"
        emoji = "✅"
        desc = "ความเสี่ยงเกณฑ์ปกติ"
        advice = [
            "• ปฏิบัติตัวตามคำแนะนำทั่วไป",
            "• ดูแลสุขภาพให้ดี",
            "• รายงานอาการถ้ามีอาการผิดปกติ"
        ]
    
    diseases_str = ", ".join(disease_normalized) if disease_normalized else "ไม่มีโรคประจำตัว"
    
    message = f"{emoji} ผลประเมินความเสี่ยงส่วนบุคคล\n"
    message += "=" * 35 + "\n\n"
    message += "👤 ข้อมูลพื้นฐาน:\n"
    message += f"  • อายุ: {age_val if age_val is not None else '-'} ปี\n"
    message += f"  • น้ำหนัก: {weight_val if weight_val is not None else '-'} กก.\n"
    message += f"  • ส่วนสูง: {height_cm if height_cm is not None else '-'} ซม.\n"
    message += f"  • BMI: {bmi:.1f}\n"
    message += f"  • โรคประจำตัว: {diseases_str}\n\n"
    
    message += "📊 ปัจจัยความเสี่ยง:\n"
    for factor in risk_factors:
        message += f"  {factor}\n"
    
    message += f"\n⚠️ ระดับความเสี่ยง: {risk_level}\n"
    message += f"(คะแนนรวม: {risk_score})\n\n"
    message += f"📝 {desc}\n\n"
    message += "💡 คำแนะนำ:\n"
    for adv in advice:
        message += f"  {adv}\n"

    return PersonalClinicalOutput(
        risk_score=risk_score,
        risk_level=risk_level,
        risk_factors=risk_factors,
        bmi=bmi,
        diseases_normalized=disease_normalized,
        description=desc,
        advice=advice,
        patient_message=message,
        notification_required=(risk_score >= 4)
    )
