# -*- coding: utf-8 -*-
"""
Risk Assessment Service Module
Handles symptom and personal risk calculations
"""
import json
from config import (
    get_logger,
    RISK_DISEASES,
    DISEASE_MAPPING,
    DISEASE_NEGATIVES
)
from database import save_symptom_data, save_profile_data
from services.notification import (
    send_line_push,
    build_symptom_notification,
    build_risk_notification
)

logger = get_logger(__name__)

# Pre-sorted disease keys (longest first) — avoids re-sorting on every call
_SORTED_DISEASE_KEYS = sorted(DISEASE_MAPPING.keys(), key=lambda x: -len(x))


def calculate_symptom_risk(user_id, pain, wound, fever, mobility, neuro=None):
    """
    Calculate symptom-based risk score.

    Phase 2-A: neuro-symptom branch (numbness, weakness). Optional so the
    webhook can omit it for backward compatibility with existing callers.

    Returns:
        str: Formatted message with risk assessment
    """
    risk_score = 0
    risk_details = []
    
    # Pain Score Analysis
    try:
        p_val = int(pain) if pain is not None and str(pain).strip() != "" else 0
    except (ValueError, TypeError):  # Bug #8 fix: was bare except:
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
    wound_text = str(wound or "").lower()
    if any(x in wound_text for x in ["หนอง", "มีกลิ่น", "แฉะ", "pus", "discharge"]):
        risk_score += 3
        risk_details.append("🔴 แผลมีหนองหรือมีกลิ่น - ต้องพบแพทย์ทันที!")
    elif any(x in wound_text for x in ["บวมแดง", "อักเสบ", "swelling", "red", "inflamed"]):
        risk_score += 2
        risk_details.append("🟡 แผลบวมแดงอักเสบ")
    elif any(x in wound_text for x in ["ปกติ", "ดี", "แห้ง", "normal", "dry", "good"]):
        risk_details.append("🟢 สภาพแผลปกติ")
    
    # Fever Check
    fever_text = str(fever or "").lower()
    if any(x in fever_text for x in ["มี", "ตัวร้อน", "fever", "hot", "ไข้"]):
        risk_score += 2
        risk_details.append("🔴 มีไข้ - อาจมีการติดเชื้อ")
    else:
        risk_details.append("🟢 ไม่มีไข้")
    
    # Mobility Status
    mobility_text = str(mobility or "").lower()
    if any(x in mobility_text for x in ["ไม่ได้", "ติดเตียง", "ไม่เดิน", "cannot", "bedridden"]):
        risk_score += 1
        risk_details.append("🟡 เคลื่อนไหวลำบาก")
    elif any(x in mobility_text for x in ["เดินได้", "ปกติ", "normal", "can walk"]):
        risk_details.append("🟢 เคลื่อนไหวได้ปกติ")

    # Neuro Symptoms (Phase 2-A): numbness / weakness flags post-op red flags
    # for orthopedic and neurosurgical cases per doc #1 § 2.2.
    neuro_text = str(neuro or "").lower()
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

    # Risk Level Classification
    if risk_score >= 5:
        risk_level = "🚨 อันตราย - ต้องพบแพทย์ทันที!"
        emoji = "🚨"
        action = "กรุณาติดต่อพยาบาลหรือมาโรงพยาบาลทันที!"
        color = "🔴"
    elif risk_score >= 3:
        risk_level = "⚠️ เสี่ยงสูง"
        emoji = "⚠️"
        action = "กรุณากดปุ่ม 'ปรึกษาพยาบาล' หรือโทรติดต่อทันที"
        color = "🟠"
    elif risk_score >= 2:
        risk_level = "🟡 เสี่ยงปานกลาง"
        emoji = "🟡"
        action = "เฝ้าระวังอาการใกล้ชิด 24 ชม. ถ้าอาการแย่กรุณาติดต่อ"
        color = "🟡"
    elif risk_score == 1:
        risk_level = "🟢 เสี่ยงต่ำ (เฝ้าระวัง)"
        emoji = "🟢"
        action = "โดยรวมปกติดี แต่ต้องสังเกตอาการต่อไป"
        color = "🟢"
    else:
        risk_level = "✅ ปกติดี"
        emoji = "✅"
        action = "แผลหายดี ยอดเยี่ยมมาก! กรุณารายงานอาการต่อเนื่อง"
        color = "🟢"
    
    # Build message
    message = f"{emoji} ผลประเมินอาการ\n"
    message += "=" * 30 + "\n\n"
    message += "📋 รายละเอียด:\n"
    for detail in risk_details:
        message += f"  {detail}\n"
    message += f"\n{color} ระดับความเสี่ยง: {risk_level}\n"
    message += f"(คะแนนรวม: {risk_score})\n\n"
    message += f"💡 คำแนะนำ:\n{action}"
    
    # Save to sheet
    save_symptom_data(user_id, pain, wound, fever, mobility, risk_level, risk_score)
    
    # Send notification if high risk
    if risk_score >= 3:
        notify_msg = build_symptom_notification(
            user_id, pain, wound, fever, mobility, risk_level, risk_score
        )
        send_line_push(notify_msg)
    
    return message


def normalize_diseases(disease_param):
    """
    Extract and normalize disease names from various formats
    
    Returns:
        list: Normalized disease names
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
        
        # Skip negatives
        if s in DISEASE_NEGATIVES or any(neg in s for neg in ["no disease", "ไม่มี"]):
            continue
        
        # Try to map to standard disease name
        found = False
        for key in _SORTED_DISEASE_KEYS:
            if key in s:
                canon = DISEASE_MAPPING[key]
                if canon not in seen:
                    normalized.append(canon)
                    seen.add(canon)
                found = True
                break
        
        # Keep original if no mapping found
        if not found:
            candidate = raw.strip()
            if candidate and candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
    
    return normalized


def calculate_personal_risk(user_id, age, weight, height, disease):
    """
    Calculate personal health risk based on demographics and conditions
    
    Returns:
        str: Formatted message with risk assessment
    """
    risk_score = 0
    risk_factors = []
    bmi = 0.0
    
    # Parse inputs
    try:
        age_val = int(age) if age is not None and str(age).strip() != "" else None
    except (ValueError, TypeError):  # Bug #8 fix: was bare except:
        age_val = None
    
    try:
        weight_val = float(weight) if weight is not None and str(weight).strip() != "" else None
    except (ValueError, TypeError):  # Bug #8 fix
        weight_val = None
    
    try:
        height_cm = float(height) if height is not None and str(height).strip() != "" else None
    except (ValueError, TypeError):  # Bug #8 fix
        height_cm = None
    
    # Calculate BMI
    if height_cm and weight_val and height_cm > 0:
        height_m = height_cm / 100.0
        bmi = weight_val / (height_m ** 2)
    
    # Age Risk Factor
    if age_val is not None:
        if age_val >= 70:
            risk_score += 2
            risk_factors.append(f"🔴 อายุ {age_val} ปี (สูงอายุมาก)")
        elif age_val >= 60:
            risk_score += 1
            risk_factors.append(f"🟡 อายุ {age_val} ปี (สูงอายุ)")
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
    disease_normalized = normalize_diseases(disease)
    logger.debug("Normalized diseases: %s", disease_normalized)
    
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
    
    # Build message
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
    
    # Save to sheet
    save_profile_data(user_id, age_val, weight_val, height_cm, bmi, 
                      disease_normalized, risk_level, risk_score)
    
    # Send notification if high risk
    if risk_score >= 4:
        notify_msg = build_risk_notification(
            user_id,
            age_val if age_val is not None else "ไม่ระบุ",
            bmi,
            diseases_str,
            risk_level,
            risk_score
        )
        send_line_push(notify_msg)
    
    return message
