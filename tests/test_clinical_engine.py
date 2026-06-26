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
        # pain:3 + wound:3 + fever:2 + mobility:1 + neuro:3 = 12
        self.assertEqual(res.risk_score, 12)
        self.assertEqual(res.risk_code, "critical")
        self.assertTrue(res.notification_required)
        self.assertIn("🚨 อันตราย", res.risk_label)


class TestClinicalEnginePersonalRisk(unittest.TestCase):
    def test_normalize_diseases_negative(self):
        self.assertEqual(normalize_diseases("ไม่มี"), [])
        self.assertEqual(normalize_diseases("no disease"), [])

    def test_normalize_diseases_mapping(self):
        self.assertEqual(normalize_diseases("เป็นเบาหวานค่ะ"), ["เบาหวาน"])
        self.assertEqual(normalize_diseases(["ความดันสูง", "หัวใจ"]), ["ความดัน", "หัวใจ"])

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
            disease=["เบาหวาน", "ความดันสูง"] # high risk diseases
        )
        res = evaluate_personal_risk(inputs)
        # age:2 + BMI:2 + diseases:3 = 7
        self.assertEqual(res.risk_score, 7)
        self.assertEqual(res.risk_level, "🔴 สูงมาก (Very High Risk)")
        self.assertTrue(res.notification_required)


if __name__ == "__main__":
    unittest.main()
