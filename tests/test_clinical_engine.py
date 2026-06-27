# -*- coding: utf-8 -*-
"""
Unit tests for the Pure Clinical Risk Engine (KWN-10).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("NURSE_GROUP_ID", "test_nurse_group")

from services.clinical_engine import (
    SymptomClinicalInput,
    evaluate_symptom_risk,
    PersonalClinicalInput,
    evaluate_personal_risk,
    normalize_diseases,
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
        self.assertEqual(res.risk_code, "normal")
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
        self.assertEqual(res.risk_code, "high")
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
        # pain:3 + wound:3 + fever:2 + mobility:1 (no sudden keywords) + neuro:3 = 12
        self.assertEqual(res.risk_score, 12)
        self.assertEqual(res.risk_code, "critical")
        self.assertTrue(res.notification_required)
        self.assertIn("🚨 อันตราย", res.risk_label)

    def test_symptom_risk_sudden_mobility_loss_critical(self):
        """
        HF-3: Sudden inability to walk (กะทันหันเดินไม่ได้) must be Critical (+3),
        not just +1, since it may indicate DVT or dislocation.
        """
        inputs = SymptomClinicalInput(
            pain=2,
            wound="ปกติ",
            fever="ไม่มี",
            mobility="เดินไม่ได้กะทันหัน",
            neuro="ไม่มี"
        )
        res = evaluate_symptom_risk(inputs)
        # pain:0 + wound:0 + fever:0 + mobility:3 (กะทันหัน) + neuro:0 = 3
        self.assertEqual(res.risk_score, 3)
        self.assertEqual(res.risk_code, "high")
        self.assertTrue(res.notification_required)
        self.assertTrue(any("สูญเสียการเคลื่อนไหวอย่างกะทันหัน" in d for d in res.risk_details))


class TestClinicalEnginePersonalRisk(unittest.TestCase):
    def test_normalize_diseases_negative(self):
        self.assertEqual(normalize_diseases("ไม่มี"), [])
        self.assertEqual(normalize_diseases("no disease"), [])

    def test_normalize_diseases_mapping(self):
        self.assertEqual(normalize_diseases("เป็นเบาหวานค่ะ"), ["เบาหวาน"])
        self.assertEqual(normalize_diseases(["ความดันสูง", "หัวใจ"]), ["ความดัน", "หัวใจ"])

    def test_normalize_diseases_multi_in_single_string(self):
        """
        HF-1: A freetext string containing multiple diseases must return ALL of them,
        not just the first one that matches (the old `break` bug).
        """
        result = normalize_diseases("เบาหวาน ความดัน")
        self.assertIn("เบาหวาน", result)
        self.assertIn("ความดัน", result)
        self.assertEqual(len(result), 2)

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
            age=67,  # HF-2: now ≥65 = high risk (+2, was ≥70)
            weight=100.0,
            height=160.0, # BMI = 39.06 (obese)
            disease=["เบาหวาน", "ความดันสูง"] # high risk diseases
        )
        res = evaluate_personal_risk(inputs)
        # age:2 + BMI:2 + diseases:3 = 7
        self.assertEqual(res.risk_score, 7)
        self.assertEqual(res.risk_level, "🔴 สูงมาก (Very High Risk)")
        self.assertTrue(res.notification_required)

    def test_personal_risk_age_65_is_high(self):
        """
        HF-2: Patients aged 65-69 must now be classified as high-risk (+2),
        consistent with Geriatric Medicine guidelines (PMID 40223829).
        """
        inputs = PersonalClinicalInput(
            age=65,
            weight=60.0,
            height=170.0,
            disease="ไม่มี"
        )
        res = evaluate_personal_risk(inputs)
        # age 65 → +2, BMI normal → +0, no disease → +0 = 2
        self.assertEqual(res.risk_score, 2)
        # score=2 is Moderate risk level
        self.assertEqual(res.risk_level, "🟡 ปานกลาง (Moderate Risk)")

    def test_personal_risk_freetext_comorbidities_not_undertriaged(self):
        """
        HF-1: When patient types diseases in a single freetext string like "เบาหวาน ความดัน",
        both diseases must be detected and scored as multi-comorbidity (+3).
        """
        inputs = PersonalClinicalInput(
            age=50,
            weight=65.0,
            height=165.0,
            disease="เบาหวาน ความดัน"  # single string with two diseases
        )
        res = evaluate_personal_risk(inputs)
        # diseases: 2 high-risk → +3, age: 50 → +0, BMI normal → +0 = 3
        self.assertEqual(res.risk_score, 3)
        self.assertEqual(len(res.diseases_normalized), 2)
        self.assertIn("เบาหวาน", res.diseases_normalized)
        self.assertIn("ความดัน", res.diseases_normalized)


if __name__ == "__main__":
    unittest.main()
