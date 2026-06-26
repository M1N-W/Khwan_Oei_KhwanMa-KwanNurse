# Clinical Risk Engine (KWN-10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the clinical risk evaluation logic into a pure, deterministic engine (`services/clinical_engine.py`) and refactor `services/risk_assessment.py` to delegate calculations to it.

**Architecture:** We will create `SymptomClinicalInput`, `SymptomClinicalOutput`, `PersonalClinicalInput`, and `PersonalClinicalOutput` dataclasses in `services/clinical_engine.py`. The calculation logic will be extracted into pure functions. The existing `services/risk_assessment.py` functions will become side-effect wrappers for Google Sheets saving, LINE notifications, and auditing.

**Tech Stack:** Python 3.12, standard dataclasses, unittest framework.

---

### Task 1: Create the pure clinical engine and write tests for symptom evaluation

**Files:**
- Create: `services/clinical_engine.py`
- Create: `tests/test_clinical_engine.py`

- [ ] **Step 1: Write the dataclasses and symptom evaluation function**

Create `services/clinical_engine.py` with the following content:
```python
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Optional, List
from config import get_logger

logger = get_logger(__name__)

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

def evaluate_symptom_risk(inputs: SymptomClinicalInput) -> SymptomClinicalOutput:
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
    mobility_text = str(inputs.mobility or "").lower()
    if any(x in mobility_text for x in ["ไม่ได้", "ติดเตียง", "ไม่เดิน", "cannot", "bedridden"]):
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
```

- [ ] **Step 2: Create tests for `evaluate_symptom_risk`**

Create `tests/test_clinical_engine.py` with the following content:
```python
# -*- coding: utf-8 -*-
import os
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Mock config dependencies before import
os.environ.setdefault("NURSE_GROUP_ID", "test_nurse_group")

from services.clinical_engine import (
    SymptomClinicalInput,
    evaluate_symptom_risk,
)

class TestClinicalEngineSymptomRisk(unittest.TestCase):
    def test_symptom_risk_normal(self):
        inputs = SymptomClinicalInput(
            pain=0,
            wound="ดี แห้ง ปกติ",
            fever="ไม่มี",
            mobility="ปกติ",
            neuro="ปกติ"
        )
        res = evaluate_symptom_risk(inputs)
        self.assertEqual(res.risk_score, 0)
        self.assertEqual(res.risk_code, "green_ok")
        self.assertFalse(res.notification_required)
        self.assertIn("✅ ปกติดี", res.risk_label)

    def test_symptom_risk_critical_pain_only(self):
        inputs = SymptomClinicalInput(
            pain=9,
            wound="ดี แห้ง ปกติ",
            fever="ไม่มี",
            mobility="ปกติ",
            neuro="ปกติ"
        )
        res = evaluate_symptom_risk(inputs)
        self.assertEqual(res.risk_score, 3)
        self.assertEqual(res.risk_code, "orange_high")
        self.assertTrue(res.notification_required)
        self.assertIn("⚠️ เสี่ยงสูง", res.risk_label)

    def test_symptom_risk_danger_all_red(self):
        inputs = SymptomClinicalInput(
            pain=9,
            wound="หนองและอักเสบ",
            fever="มีไข้ตัวร้อน",
            mobility="ไม่ได้ ติดเตียง",
            neuro="อ่อนแรงขยับไม่ได้"
        )
        res = evaluate_symptom_risk(inputs)
        # pain:3 + wound:3 + fever:2 + mobility:1 + neuro:3 = 12
        self.assertEqual(res.risk_score, 12)
        self.assertEqual(res.risk_code, "red_danger")
        self.assertTrue(res.notification_required)
        self.assertIn("🚨 อันตราย", res.risk_label)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the new tests**

Run: `python -m unittest tests/test_clinical_engine.py -v`
Expected: PASS 3 tests.

- [ ] **Step 4: Commit**

```bash
git add services/clinical_engine.py tests/test_clinical_engine.py
git commit -m "feat(kwn-10): add pure symptom evaluation logic and unit tests"
```

---

### Task 2: Implement disease normalization and personal risk logic in the engine and write tests

**Files:**
- Modify: `services/clinical_engine.py`
- Modify: `tests/test_clinical_engine.py`

- [ ] **Step 1: Implement personal risk dataclasses and evaluation**

Append to `services/clinical_engine.py`:
```python
import json
from config import RISK_DISEASES, DISEASE_MAPPING, DISEASE_NEGATIVES

_SORTED_DISEASE_KEYS = sorted(DISEASE_MAPPING.keys(), key=lambda x: -len(x))

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

def normalize_diseases(disease_param) -> List[str]:
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
        
        found = False
        for key in _SORTED_DISEASE_KEYS:
            if key in s:
                canon = DISEASE_MAPPING[key]
                if canon not in seen:
                    normalized.append(canon)
                    seen.add(canon)
                found = True
                break
        
        if not found:
            candidate = raw.strip()
            if candidate and candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
    
    return normalized

def evaluate_personal_risk(inputs: PersonalClinicalInput) -> PersonalClinicalOutput:
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
    
    if height_cm and weight_val and height_cm > 0:
        height_m = height_cm / 100.0
        bmi = weight_val / (height_m ** 2)
    
    if age_val is not None:
        if age_val >= 70:
            risk_score += 2
            risk_factors.append(f"🔴 อายุ {age_val} ปี (สูงอายุมาก)")
        elif age_val >= 60:
            risk_score += 1
            risk_factors.append(f"🟡 อายุ {age_val} ปี (สูงอายุ)")
        else:
            risk_factors.append(f"🟢 อายุ {age_val} ปี (ปกติ)")
    
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
```

- [ ] **Step 2: Append tests for `evaluate_personal_risk`**

Append to `tests/test_clinical_engine.py`:
```python
from services.clinical_engine import (
    PersonalClinicalInput,
    evaluate_personal_risk,
    normalize_diseases,
)

class TestClinicalEnginePersonalRisk(unittest.TestCase):
    def test_normalize_diseases_negative(self):
        self.assertEqual(normalize_diseases("ไม่มี"), [])
        self.assertEqual(normalize_diseases("no disease"), [])

    def test_normalize_diseases_mapping(self):
        self.assertEqual(normalize_diseases("เป็นเบาหวานค่ะ"), ["Diabetes"])
        self.assertEqual(normalize_diseases(["ความดันสูง", "หัวใจ"]), ["Hypertension", "Heart Disease"])

    def test_personal_risk_low(self):
        inputs = PersonalClinicalInput(
            age=25,
            weight=60.0,
            height=170.0,
            disease="ไม่มี"
        )
        res = evaluate_personal_risk(inputs)
        self.assertEqual(res.risk_score, 0)
        self.assertEqual(res.risk_level, "🟢 ต่ำ (Low Risk)")
        self.assertFalse(res.notification_required)

    def test_personal_risk_high(self):
        inputs = PersonalClinicalInput(
            age=72,
            weight=100.0,
            height=160.0, # BMI = 39.06 (obese)
            disease="เบาหวาน ความดันสูง" # high risk diseases
        )
        res = evaluate_personal_risk(inputs)
        # age:2 + BMI:2 + diseases:3 = 7
        self.assertEqual(res.risk_score, 7)
        self.assertEqual(res.risk_level, "🔴 สูงมาก (Very High Risk)")
        self.assertTrue(res.notification_required)
```

- [ ] **Step 3: Run personal risk tests**

Run: `python -m unittest tests/test_clinical_engine.py -v`
Expected: PASS 7 tests.

- [ ] **Step 4: Commit**

```bash
git add services/clinical_engine.py tests/test_clinical_engine.py
git commit -m "feat(kwn-10): add pure personal risk evaluation and unit tests"
```

---

### Task 3: Refactor existing `services/risk_assessment.py` to use `services/clinical_engine.py`

**Files:**
- Modify: `services/risk_assessment.py`

- [ ] **Step 1: Modify `services/risk_assessment.py` to import and wrap clinical engine**

Replace the implementation of `calculate_symptom_risk_outcome` and `calculate_personal_risk` to use `evaluate_symptom_risk` and `evaluate_personal_risk`. Keep all sheets database persistence, LINE notifications, and audit checks inside `services/risk_assessment.py`.

In `services/risk_assessment.py`:
```python
# Replace imports & update functions
from services.clinical_engine import (
    SymptomClinicalInput,
    evaluate_symptom_risk,
    PersonalClinicalInput,
    evaluate_personal_risk,
    normalize_diseases
)
```

And refactor the core functions:
```python
def calculate_symptom_risk_outcome(user_id, pain, wound, fever, mobility, neuro=None):
    """
    Calculate symptom-based risk score.
    Delegates calculation to evaluate_symptom_risk pure logic.
    """
    inputs = SymptomClinicalInput(
        pain=pain,
        wound=wound,
        fever=fever,
        mobility=mobility,
        neuro=neuro
    )
    engine_out = evaluate_symptom_risk(inputs)
    risk_score = engine_out.risk_score
    risk_code = engine_out.risk_code
    risk_label = engine_out.risk_label
    message = engine_out.patient_message

    # Save to sheet. Treat both False and unexpected exceptions as failures.
    try:
        save_succeeded = bool(
            save_symptom_data(user_id, pain, wound, fever, mobility, risk_code, risk_score)
        )
    except Exception:
        save_succeeded = False
        logger.exception(
            "Symptom assessment save raised risk_code=%s risk_score=%s",
            risk_code, risk_score,
        )

    if not save_succeeded:
        _metric("symptom_assessment.save_failed")
        logger.warning(
            "Symptom assessment save not confirmed risk_code=%s risk_score=%s",
            risk_code, risk_score,
        )

    # Send notification if high risk
    notification_required = engine_out.notification_required
    notification_succeeded = None
    failed_alert_persisted = None
    if notification_required:
        notify_msg = build_symptom_notification(
            user_id, pain, wound, fever, mobility, risk_label, risk_score
        )
        try:
            notification_succeeded = bool(send_line_push(notify_msg))
        except Exception:
            notification_succeeded = False
            logger.exception(
                "Symptom assessment notification raised risk_code=%s risk_score=%s",
                risk_code, risk_score,
            )

        if not notification_succeeded:
            _metric("symptom_assessment.notify_failed")
            logger.warning(
                "Symptom assessment notification not confirmed risk_code=%s risk_score=%s",
                risk_code, risk_score,
            )
            try:
                failed_alert_persisted = bool(save_failed_symptom_alert(
                    user_id=user_id,
                    risk_code=risk_code,
                    risk_score=risk_score,
                    pain=pain,
                    wound=wound,
                    fever=fever,
                    mobility=mobility,
                    neuro=neuro,
                    notification_message=notify_msg or "",
                ))
            except Exception:
                failed_alert_persisted = False

            if failed_alert_persisted:
                _metric("symptom_assessment.failed_alert_persisted")
            else:
                _metric("symptom_assessment.failed_alert_persist_failed")

    if (not save_succeeded) or (notification_required and notification_succeeded is False):
        _metric("symptom_assessment.partial_failure")

    if save_succeeded:
        try:
            from services.early_warning import check_user_early_warning
            check_user_early_warning(user_id)
        except Exception:
            logger.exception("Early-warning check failed for %s", user_id)
    else:
        _metric("symptom_assessment.early_warning_skipped_save_failed")

    message = _append_symptom_reliability_notice(
        message=message,
        save_succeeded=save_succeeded,
        notification_required=notification_required,
        notification_succeeded=notification_succeeded,
    )

    return SymptomAssessmentOutcome(
        message=message,
        risk_code=risk_code,
        risk_score=risk_score,
        save_succeeded=save_succeeded,
        notification_required=notification_required,
        notification_succeeded=notification_succeeded,
        failed_alert_persisted=failed_alert_persisted,
    )
```

And refactor `calculate_personal_risk`:
```python
def calculate_personal_risk(user_id, age, weight, height, disease):
    """
    Calculate personal health risk based on demographics and conditions.
    Delegates calculation to evaluate_personal_risk pure logic.
    """
    inputs = PersonalClinicalInput(
        age=age,
        weight=weight,
        height=height,
        disease=disease
    )
    engine_out = evaluate_personal_risk(inputs)
    risk_score = engine_out.risk_score
    risk_level = engine_out.risk_level
    bmi = engine_out.bmi
    disease_normalized = engine_out.diseases_normalized
    message = engine_out.patient_message

    # Save to sheet
    save_profile_data(user_id, inputs.age, inputs.weight, inputs.height, bmi, 
                      disease_normalized, risk_level, risk_score)
    
    # Send notification if high risk
    if engine_out.notification_required:
        diseases_str = ", ".join(disease_normalized) if disease_normalized else "ไม่มีโรคประจำตัว"
        notify_msg = build_risk_notification(
            user_id,
            inputs.age if inputs.age is not None else "ไม่ระบุ",
            bmi,
            diseases_str,
            risk_level,
            risk_score
        )
        send_line_push(notify_msg)
    
    return message
```

- [ ] **Step 2: Run targeted tests**

Run: `python -m unittest tests/test_symptom_risk.py tests/test_clinical_engine.py -v`
Expected: PASS

- [ ] **Step 3: Run the full regression test suite**

Run: `python run_regression_tests.py`
Expected: ALL 610+ TESTS PASS successfully.

- [ ] **Step 4: Commit**

```bash
git add services/risk_assessment.py
git commit -m "refactor(kwn-10): delegate calculations from risk_assessment to clinical_engine"
```
