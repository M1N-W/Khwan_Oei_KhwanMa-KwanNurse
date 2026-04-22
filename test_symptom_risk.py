# -*- coding: utf-8 -*-
"""
Phase 2-A regression tests: neuro-symptom branch in calculate_symptom_risk().

Run: python -m unittest test_symptom_risk.py -v
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


class NeuroSymptomTests(unittest.TestCase):
    """All tests patch persistence + notification so no external I/O happens."""

    def _run_symptom(self, **kwargs):
        # Default all required params to low-risk values so we isolate neuro.
        # NOTE: fever uses "ปกติ" rather than "ไม่มี" because the pre-existing
        # keyword matcher treats "ไม่มี" as containing the substring "มี"
        # which would add +2. That is an unrelated quirk; this test isolates
        # the neuro branch by avoiding the substring collision.
        defaults = {
            "user_id": "test_user",
            "pain": 2,
            "wound": "ปกติ",
            "fever": "ปกติ",
            "mobility": "เดินได้",
        }
        defaults.update(kwargs)
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=True):
            from services.risk_assessment import calculate_symptom_risk
            return calculate_symptom_risk(**defaults)

    def test_weakness_triggers_high_risk_alert(self):
        msg = self._run_symptom(neuro="มีกล้ามเนื้ออ่อนแรงที่ขา")
        self.assertIn("กล้ามเนื้ออ่อนแรง", msg)
        # weakness adds +3 → risk_score >= 3 → เสี่ยงสูง or อันตราย header
        self.assertTrue("เสี่ยงสูง" in msg or "อันตราย" in msg)

    def test_numbness_triggers_medium_risk(self):
        msg = self._run_symptom(neuro="มีอาการชาที่ปลายเท้า")
        self.assertIn("อาการชา", msg)
        # +2 from neuro alone → medium risk band
        self.assertIn("เสี่ยงปานกลาง", msg)

    def test_radiating_pain_flagged(self):
        msg = self._run_symptom(neuro="ปวดร้าวลงขา")
        self.assertIn("ปวดร้าว", msg)

    def test_explicit_no_neuro_is_positive_signal(self):
        msg = self._run_symptom(neuro="ไม่มี")
        self.assertIn("ไม่มีอาการทางระบบประสาท", msg)

    def test_missing_neuro_stays_backward_compatible(self):
        # Omit neuro entirely — original 4-arg behavior must still work.
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=True):
            from services.risk_assessment import calculate_symptom_risk
            msg = calculate_symptom_risk("u", 2, "ปกติ", "ไม่มี", "เดินได้")
        self.assertIn("ผลประเมินอาการ", msg)
        self.assertNotIn("ระบบประสาท", msg)

    def test_weakness_combined_with_pus_escalates_to_danger(self):
        msg = self._run_symptom(wound="แผลมีหนอง", neuro="อ่อนแรง")
        # wound_pus (+3) + weakness (+3) >= 5 → danger tier
        self.assertIn("อันตราย", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
