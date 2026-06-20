# -*- coding: utf-8 -*-
"""Reliability contract tests for structured symptom assessment."""
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


class SymptomAssessmentOutcomeTests(unittest.TestCase):

    def setUp(self):
        from services.metrics import reset
        reset()

    def _assess(self, *, save=True, push=True, backlog=True, **overrides):
        defaults = {
            "user_id": "U-reliable",
            "pain": 9,
            "wound": "ปกติ",
            "fever": "ไม่มี",
            "mobility": "เดินได้",
            "neuro": None,
        }
        defaults.update(overrides)
        with patch("services.risk_assessment.save_symptom_data", return_value=save) as save_mock, \
             patch("services.risk_assessment.send_line_push", return_value=push) as push_mock, \
             patch("services.risk_assessment.save_failed_symptom_alert", return_value=backlog) as backlog_mock, \
             patch("services.early_warning.check_user_early_warning") as early_mock:
            from services.risk_assessment import calculate_symptom_risk_outcome
            outcome = calculate_symptom_risk_outcome(**defaults)
        return outcome, save_mock, push_mock, backlog_mock, early_mock

    def test_calculate_symptom_risk_remains_string_api(self):
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=True), \
             patch("services.early_warning.check_user_early_warning"):
            from services.risk_assessment import calculate_symptom_risk
            result = calculate_symptom_risk("U-compat", 1, "ปกติ", "ไม่มี", "เดินได้")

        self.assertIsInstance(result, str)
        self.assertIn("ผลประเมินอาการ", result)

    def test_structured_outcome_success_fields_and_message_unchanged(self):
        outcome, save, push, backlog, early = self._assess()

        self.assertEqual(outcome.risk_code, "high")
        self.assertEqual(outcome.risk_score, 3)
        self.assertTrue(outcome.save_succeeded)
        self.assertTrue(outcome.notification_required)
        self.assertTrue(outcome.notification_succeeded)
        self.assertIsNone(outcome.failed_alert_persisted)
        self.assertIn("กรุณากดปุ่ม 'ปรึกษาพยาบาล' หรือโทรติดต่อทันที", outcome.message)
        self.assertNotIn("ไม่สามารถบันทึก", outcome.message)
        self.assertNotIn("ไม่สามารถยืนยัน", outcome.message)
        save.assert_called_once()
        push.assert_called_once()
        backlog.assert_not_called()
        early.assert_called_once_with("U-reliable")

    def test_low_risk_save_failure_does_not_notify_and_skips_early_warning(self):
        outcome, save, push, backlog, early = self._assess(
            save=False,
            pain=6,
            wound="ปกติ",
            fever="ไม่มี",
            mobility="เดินได้",
        )

        self.assertEqual(outcome.risk_code, "low")
        self.assertFalse(outcome.save_succeeded)
        self.assertFalse(outcome.notification_required)
        self.assertIsNone(outcome.notification_succeeded)
        self.assertIsNone(outcome.failed_alert_persisted)
        self.assertIn("ประเมินอาการเรียบร้อย", outcome.message)
        self.assertIn("ยังไม่สามารถยืนยันการบันทึกประวัติ", outcome.message)
        self.assertIn("ลองรายงานอาการอีกครั้ง", outcome.message)
        push.assert_not_called()
        backlog.assert_not_called()
        early.assert_not_called()

        from services.metrics import snapshot
        snap = snapshot()
        self.assertEqual(snap.get("symptom_assessment.save_failed"), 1)
        self.assertEqual(snap.get("symptom_assessment.partial_failure"), 1)
        self.assertEqual(snap.get("symptom_assessment.early_warning_skipped_save_failed"), 1)
        self.assertIsNone(snap.get("symptom_assessment.notify_failed"))

    def test_high_risk_save_failure_still_pushes_successfully(self):
        outcome, save, push, backlog, early = self._assess(save=False, push=True)

        self.assertFalse(outcome.save_succeeded)
        self.assertTrue(outcome.notification_required)
        self.assertTrue(outcome.notification_succeeded)
        self.assertIsNone(outcome.failed_alert_persisted)
        self.assertIn("ส่งแจ้งเตือนพยาบาลแล้ว", outcome.message)
        self.assertIn("ยังไม่สามารถยืนยันการบันทึกรายงาน", outcome.message)
        push.assert_called_once()
        backlog.assert_not_called()
        early.assert_not_called()

        from services.metrics import snapshot
        snap = snapshot()
        self.assertEqual(snap.get("symptom_assessment.save_failed"), 1)
        self.assertEqual(snap.get("symptom_assessment.partial_failure"), 1)
        self.assertIsNone(snap.get("symptom_assessment.notify_failed"))

    def test_push_failure_after_save_persists_failed_alert_and_guides_contact(self):
        outcome, save, push, backlog, early = self._assess(save=True, push=False, backlog=True)

        self.assertTrue(outcome.save_succeeded)
        self.assertTrue(outcome.notification_required)
        self.assertFalse(outcome.notification_succeeded)
        self.assertTrue(outcome.failed_alert_persisted)
        self.assertIn("บันทึกรายงานไว้แล้ว", outcome.message)
        self.assertIn("ยังไม่สามารถยืนยันว่าแจ้งพยาบาลสำเร็จ", outcome.message)
        self.assertIn("กดปุ่ม 'ปรึกษาพยาบาล'", outcome.message)
        backlog.assert_called_once()
        early.assert_called_once_with("U-reliable")

        from services.metrics import snapshot
        snap = snapshot()
        self.assertEqual(snap.get("symptom_assessment.notify_failed"), 1)
        self.assertEqual(snap.get("symptom_assessment.partial_failure"), 1)
        self.assertEqual(snap.get("symptom_assessment.failed_alert_persisted"), 1)

    def test_both_save_and_push_failure_do_not_claim_success(self):
        outcome, save, push, backlog, early = self._assess(save=False, push=False, backlog=False)

        self.assertFalse(outcome.save_succeeded)
        self.assertFalse(outcome.notification_succeeded)
        self.assertFalse(outcome.failed_alert_persisted)
        self.assertIn("ยังไม่สามารถยืนยันการบันทึกรายงาน", outcome.message)
        self.assertIn("ยังไม่สามารถยืนยันว่าแจ้งพยาบาลสำเร็จ", outcome.message)
        self.assertNotIn("ส่งแจ้งเตือนพยาบาลแล้ว", outcome.message)
        self.assertNotIn("เข้าคิวแล้ว", outcome.message)
        self.assertNotIn("ระบบจะส่งซ้ำอัตโนมัติ", outcome.message)
        backlog.assert_called_once()
        early.assert_not_called()

        from services.metrics import snapshot
        snap = snapshot()
        self.assertEqual(snap.get("symptom_assessment.save_failed"), 1)
        self.assertEqual(snap.get("symptom_assessment.notify_failed"), 1)
        self.assertEqual(snap.get("symptom_assessment.partial_failure"), 1)
        self.assertEqual(snap.get("symptom_assessment.failed_alert_persist_failed"), 1)
        self.assertEqual(snap.get("symptom_assessment.early_warning_skipped_save_failed"), 1)

    def test_save_and_push_exceptions_convert_to_failed_outcome(self):
        with patch("services.risk_assessment.save_symptom_data", side_effect=RuntimeError("sheets down")), \
             patch("services.risk_assessment.send_line_push", side_effect=RuntimeError("line down")), \
             patch("services.risk_assessment.save_failed_symptom_alert", side_effect=RuntimeError("backlog down")), \
             patch("services.early_warning.check_user_early_warning") as early:
            from services.risk_assessment import calculate_symptom_risk_outcome
            outcome = calculate_symptom_risk_outcome(
                "U-ex", 9, "ปกติ", "ไม่มี", "เดินได้",
            )

        self.assertFalse(outcome.save_succeeded)
        self.assertFalse(outcome.notification_succeeded)
        self.assertFalse(outcome.failed_alert_persisted)
        self.assertIn("กดปุ่ม 'ปรึกษาพยาบาล'", outcome.message)
        early.assert_not_called()


class SymptomLogRetryTests(unittest.TestCase):

    def test_save_symptom_data_retries_transient_append_once_then_succeeds(self):
        from database import sheets

        class APIError(Exception):
            pass

        class Sheet:
            def __init__(self):
                self.calls = 0

            def append_row(self, row, value_input_option=None):
                self.calls += 1
                if self.calls == 1:
                    raise APIError("503 temporarily unavailable")

        sheet = Sheet()
        with patch.object(sheets, "get_worksheet", return_value=sheet), \
             patch("database.retry.time.sleep"), \
             patch("database.retry.random.uniform", return_value=0):
            result = sheets.save_symptom_data("U", 1, "ปกติ", "ไม่มี", "เดินได้", "low", 1)

        self.assertTrue(result)
        self.assertEqual(sheet.calls, 2)

    def test_save_symptom_data_non_transient_failure_does_not_retry(self):
        from database import sheets

        class Sheet:
            def __init__(self):
                self.calls = 0

            def append_row(self, row, value_input_option=None):
                self.calls += 1
                raise TypeError("programming error")

        sheet = Sheet()
        with patch.object(sheets, "get_worksheet", return_value=sheet), \
             patch("database.retry.time.sleep") as sleep:
            result = sheets.save_symptom_data("U", 1, "ปกติ", "ไม่มี", "เดินได้", "low", 1)

        self.assertFalse(result)
        self.assertEqual(sheet.calls, 1)
        sleep.assert_not_called()

    def test_save_symptom_data_exhausts_after_two_transient_attempts(self):
        from database import sheets

        class APIError(Exception):
            pass

        class Sheet:
            def __init__(self):
                self.calls = 0

            def append_row(self, row, value_input_option=None):
                self.calls += 1
                raise APIError("503 temporarily unavailable")

        sheet = Sheet()
        with patch.object(sheets, "get_worksheet", return_value=sheet), \
             patch("database.retry.time.sleep"), \
             patch("database.retry.random.uniform", return_value=0):
            result = sheets.save_symptom_data("U", 1, "ปกติ", "ไม่มี", "เดินได้", "low", 1)

        self.assertFalse(result)
        self.assertEqual(sheet.calls, 2)

    def test_save_symptom_data_missing_worksheet_returns_false(self):
        from database import sheets

        with patch.object(sheets, "get_worksheet", return_value=None):
            result = sheets.save_symptom_data("U", 1, "ปกติ", "ไม่มี", "เดินได้", "low", 1)

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
