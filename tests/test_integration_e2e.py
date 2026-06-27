# -*- coding: utf-8 -*-
"""
Phase 2 hardening: end-to-end integration tests.

Exercises the full webhook chain with all external I/O mocked:

    POST /webhook (Dialogflow payload)
      -> routes.webhook.handle_report_symptoms
      -> services.risk_assessment.calculate_symptom_risk
      -> database.save_symptom_data             (mocked)
      -> services.notification.send_line_push   (mocked, high-risk alert)
      -> services.early_warning.check_user_early_warning
      -> database.get_recent_symptom_reports    (mocked history)
      -> services.notification.send_line_push   (mocked, trend alert)

Run: python -m unittest test_integration_e2e.py -v
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
os.environ.setdefault("GSPREAD_CREDENTIALS", "")  # force can_persist=False path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _dialogflow_payload(intent_name, params, session_id="u-e2e"):
    return {
        "responseId": "test-" + intent_name,
        "session": f"projects/p/agent/sessions/{session_id}",
        "queryResult": {
            "queryText": "test",
            "parameters": params,
            "intent": {"displayName": intent_name},
        },
    }


def _make_report(days_ago=0, score=0, fever="ไม่มี", wound="ปกติ",
                 user_id="u-e2e"):
    from config import LOCAL_TZ
    ts = datetime.now(tz=LOCAL_TZ) - timedelta(days=days_ago, hours=1)
    return {
        "timestamp": ts,
        "user_id": user_id,
        "pain": 0,
        "wound": wound,
        "fever": fever,
        "mobility": "เดินได้",
        "risk_level": "ปกติ",
        "risk_score": score,
    }


class WebhookSymptomFlowTests(unittest.TestCase):
    """Full webhook → risk → early-warning chain."""

    @classmethod
    def setUpClass(cls):
        from app import create_app
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def setUp(self):
        from services.early_warning import _reset_dedup_for_tests
        from services.metrics import reset
        _reset_dedup_for_tests()
        reset()

    # -------------------------------------------------------------------
    # Happy path: low-risk report should not fire any push notifications
    # -------------------------------------------------------------------
    def test_low_risk_report_saves_no_push(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 1,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=True) as save, \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.early_warning.send_line_push") as push_warn, \
             patch("services.early_warning.get_recent_symptom_reports",
                   return_value=[_make_report(days_ago=0, score=1)]), \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("ระดับความเสี่ยง", body["fulfillmentText"])
        save.assert_called_once()
        push_risk.assert_not_called()
        push_warn.assert_not_called()

    # -------------------------------------------------------------------
    # High-risk single report fires the per-report alert
    # -------------------------------------------------------------------
    def test_high_risk_report_triggers_nurse_push(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 9,
            "wound_status": "แผลมีหนอง",
            "fever_check": "มีไข้",
            "mobility_status": "ขยับไม่ได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.early_warning.send_line_push") as push_warn, \
             patch("services.early_warning.get_recent_symptom_reports",
                   return_value=[_make_report(days_ago=0, score=5)]), \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        push_risk.assert_called_once()  # standard high-risk alert
        # Single data point → trend analysis should not fire dedicated flags
        # except silence/repeated; we only assert risk-alert was emitted.
        # Label varies ("เสี่ยงสูง" or "อันตราย") based on score tier;
        # what matters is that the user sees a risk summary and nurse was paged.
        self.assertIn("ระดับความเสี่ยง", resp.get_json()["fulfillmentText"])

    # -------------------------------------------------------------------
    # Full chain: high-risk report AND trend history → both pushes fire
    # -------------------------------------------------------------------
    def test_rising_trend_triggers_early_warning(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 8,
            "wound_status": "บวมแดง",
            "fever_check": "มีไข้",
            "mobility_status": "เดินได้",
        })
        history = [
            _make_report(days_ago=0, score=5),
            _make_report(days_ago=1, score=3),
            _make_report(days_ago=2, score=1),
        ]
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.early_warning.send_line_push") as push_warn, \
             patch("services.early_warning.get_recent_symptom_reports",
                   return_value=history), \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        push_risk.assert_called_once()
        push_warn.assert_called_once()
        warn_msg = push_warn.call_args.args[0]
        self.assertIn("Early-Warning", warn_msg)

    # -------------------------------------------------------------------
    # Missing-parameter branch: webhook returns 200 with guidance, no side
    # effects leak to downstream services.
    # -------------------------------------------------------------------
    def test_missing_params_asks_user_without_side_effects(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 5,
            # wound_status missing
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=True) as save, \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.early_warning.send_line_push") as push_warn:
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("สภาพแผล", resp.get_json()["fulfillmentText"])
        save.assert_not_called()
        push_risk.assert_not_called()
        push_warn.assert_not_called()

    # -------------------------------------------------------------------
    # Fever-negation regression at webhook layer (guards against the
    # 'ไม่มี' contains 'มี' substring trap).
    # -------------------------------------------------------------------
    def test_fever_negation_not_counted_end_to_end(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 1,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=True) as save, \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.early_warning.send_line_push") as push_warn, \
             patch("services.early_warning.get_recent_symptom_reports",
                   return_value=[_make_report(days_ago=0, fever="ไม่มี",
                                              score=0)]), \
             patch("services.early_warning.NURSE_GROUP_ID", "G123"):
            resp = self.client.post("/webhook", json=payload)

        args, _ = save.call_args
        # args order: user_id, pain, wound, fever, mobility, risk_level, score
        self.assertEqual(args[3], "ไม่มี")
        risk_score = args[6]
        self.assertLess(risk_score, 3,
                        "Fever-negation should NOT escalate risk_score")
        push_risk.assert_not_called()
        push_warn.assert_not_called()

    def test_symptom_risk_persists_canonical_code_and_dashboard_reads_alert(self):
        from config import LOCAL_TZ
        from services import dashboard_readers

        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 9,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        captured = {}

        def capture_symptom(user_id, pain, wound, fever, mobility, risk_level, risk_score):
            captured.update({
                "timestamp": datetime.now(tz=LOCAL_TZ),
                "user_id": user_id,
                "pain": pain,
                "wound": wound,
                "fever": fever,
                "mobility": mobility,
                "risk_level": risk_level,
                "risk_score": risk_score,
            })
            return True

        with patch("services.risk_assessment.save_symptom_data", side_effect=capture_symptom), \
             patch("services.risk_assessment.send_line_push", return_value=True), \
             patch("services.early_warning.get_recent_symptom_reports", return_value=[]):
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["risk_score"], 3)
        self.assertEqual(captured["risk_level"], "high")

        with patch("database.sheets.get_recent_symptom_reports", return_value=[captured]):
            alerts = dashboard_readers.get_recent_alerts(
                min_risk_level="medium",
                force_refresh=True,
            )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["risk_level"], "high")

    def test_webhook_low_risk_save_failure_returns_safe_notice(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 6,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=False), \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.risk_assessment.save_failed_symptom_alert") as backlog, \
             patch("services.early_warning.check_user_early_warning") as early:
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        text = resp.get_json()["fulfillmentText"]
        self.assertIn("ยังไม่สามารถยืนยันการบันทึกประวัติ", text)
        self.assertIn("ลองรายงานอาการอีกครั้ง", text)
        push_risk.assert_not_called()
        backlog.assert_not_called()
        early.assert_not_called()

    def test_webhook_high_risk_save_success_push_failure_guides_direct_contact(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 9,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=False) as push_risk, \
             patch("services.risk_assessment.save_failed_symptom_alert", return_value=True) as backlog, \
             patch("services.early_warning.check_user_early_warning") as early:
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        text = resp.get_json()["fulfillmentText"]
        self.assertIn("บันทึกรายงานไว้แล้ว", text)
        self.assertIn("ยังไม่สามารถยืนยันว่าแจ้งพยาบาลสำเร็จ", text)
        self.assertIn("กดปุ่ม 'ปรึกษาพยาบาล'", text)
        push_risk.assert_called_once()
        backlog.assert_called_once()
        early.assert_called_once_with("u-e2e")

    def test_webhook_high_risk_save_failure_push_success_does_not_suppress_push(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 9,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=False), \
             patch("services.risk_assessment.send_line_push", return_value=True) as push_risk, \
             patch("services.risk_assessment.save_failed_symptom_alert") as backlog, \
             patch("services.early_warning.check_user_early_warning") as early:
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        text = resp.get_json()["fulfillmentText"]
        self.assertIn("ส่งแจ้งเตือนพยาบาลแล้ว", text)
        self.assertIn("ยังไม่สามารถยืนยันการบันทึกรายงาน", text)
        push_risk.assert_called_once()
        backlog.assert_not_called()
        early.assert_not_called()

    def test_webhook_both_save_and_push_failure_returns_safe_notice(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 9,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=False), \
             patch("services.risk_assessment.send_line_push", return_value=False) as push_risk, \
             patch("services.risk_assessment.save_failed_symptom_alert", return_value=False) as backlog, \
             patch("services.early_warning.check_user_early_warning") as early:
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        text = resp.get_json()["fulfillmentText"]
        self.assertIn("ยังไม่สามารถยืนยันการบันทึกรายงาน", text)
        self.assertIn("ยังไม่สามารถยืนยันว่าแจ้งพยาบาลสำเร็จ", text)
        self.assertIn("กดปุ่ม 'ปรึกษาพยาบาล'", text)
        self.assertNotIn("ระบบจะส่งซ้ำอัตโนมัติ", text)
        push_risk.assert_called_once()
        backlog.assert_called_once()
        early.assert_not_called()

    # -------------------------------------------------------------------
    # Early-warning failure must not bubble up and break the user response.
    # -------------------------------------------------------------------
    def test_early_warning_exception_is_swallowed(self):
        payload = _dialogflow_payload("ReportSymptoms", {
            "pain_score": 2,
            "wound_status": "ปกติ",
            "fever_check": "ไม่มี",
            "mobility_status": "เดินได้",
        })
        with patch("services.risk_assessment.save_symptom_data", return_value=True), \
             patch("services.risk_assessment.send_line_push", return_value=True), \
             patch("services.early_warning.get_recent_symptom_reports",
                   side_effect=RuntimeError("sheets down")):
            resp = self.client.post("/webhook", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("ระดับความเสี่ยง", resp.get_json()["fulfillmentText"])

    # -------------------------------------------------------------------
    # Empty webhook body must return 400 without exceptions.
    # -------------------------------------------------------------------
    def test_empty_body_returns_400(self):
        resp = self.client.post("/webhook",
                                data="", content_type="application/json")
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
