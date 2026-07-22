from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class FakeWorksheet:
    def __init__(self, title, headers, rows=None):
        self.title = title
        self.values = [list(headers)] + [list(row) for row in (rows or [])]
        self.append_calls = 0

    def get_all_values(self):
        return [list(row) for row in self.values]

    def update(self, _range, values, **_kwargs):
        self.values[0] = list(values[0])

    def append_row(self, row, **_kwargs):
        self.append_calls += 1
        self.values.append(list(row))

    def batch_update(self, _updates):
        return None


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self._worksheets = worksheets

    def worksheets(self):
        return list(self._worksheets)


class SheetWriteReliabilityTests(unittest.TestCase):
    def test_appointment_replay_does_not_append_duplicate(self):
        from database.sheets import save_appointment_data

        sheet = FakeWorksheet(
            "Appointments",
            ["Timestamp", "User_ID", "Name", "Phone", "Preferred_Date",
             "Preferred_Time", "Reason", "Status", "Assigned_To", "Notes"],
        )
        with patch("database.sheets.get_worksheet", return_value=sheet):
            self.assertTrue(save_appointment_data(
                "U1", "ผู้ป่วย", "0812345678", "2026-08-01", "09:00", "ตรวจแผล",
            ))
            self.assertTrue(save_appointment_data(
                "U1", "ผู้ป่วย", "0812345678", "2026-08-01", "09:00", "ตรวจแผล",
            ))

        self.assertEqual(sheet.append_calls, 1)
        self.assertEqual(len(sheet.values) - 1, 1)
        self.assertIn("Idempotency_Key", sheet.values[0])

    def test_teleconsult_replay_returns_same_session(self):
        from database.teleconsult import create_session

        sheet = FakeWorksheet(
            "TeleconsultSessions",
            ["Session_ID", "Timestamp", "User_ID", "Issue_Type", "Priority",
             "Status", "Description", "Queue_Position", "Assigned_Nurse",
             "Started_At", "Completed_At", "Notes"],
        )
        with patch("database.teleconsult.get_worksheet", return_value=sheet):
            first = create_session("U1", "wound", 2, "แผลบวม", "request-1")
            second = create_session("U1", "wound", 2, "แผลบวม", "request-1")

        self.assertIsNotNone(first)
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual(sheet.append_calls, 1)

    def test_personal_risk_reports_save_failure(self):
        from services import risk_assessment

        with patch.object(risk_assessment, "save_profile_data", return_value=False):
            message = risk_assessment.calculate_personal_risk(
                "U1", 35, 65, 170, "ไม่มี",
            )

        self.assertIn("ไม่สามารถยืนยันการบันทึก", message)

    def test_failed_alert_replay_does_not_append_duplicate(self):
        import database.failed_nurse_alerts as alerts

        key = alerts.build_symptom_alert_idempotency_key(
            "U1", "high", 8, "5", "แผลแดง", "ไม่มีไข้", "เดินได้",
        )
        headers = list(alerts.HEADER)
        row = [""] * len(headers)
        row[headers.index("Idempotency_Key")] = key
        sheet = FakeWorksheet("FailedNurseAlerts", headers, [row])

        with patch.object(alerts, "_verify_headers_and_get_sheet", return_value=sheet):
            self.assertTrue(alerts.save_failed_symptom_alert(
                user_id="U1", risk_code="high", risk_score=8, pain="5",
                wound="แผลแดง", fever="ไม่มีไข้", mobility="เดินได้",
            ))

        self.assertEqual(sheet.append_calls, 0)


class SheetHealthTests(unittest.TestCase):
    def test_sheet_health_route_returns_probe_result(self):
        from app import create_app

        app = create_app()
        with patch(
            "database.health.check_sheet_health",
            return_value={"status": "ok", "worksheets": {}},
        ):
            response = app.test_client().get("/sheet-healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_health_check_reports_missing_worksheet_and_headers(self):
        from database.health import check_sheet_health

        worksheet = FakeWorksheet("SymptomLog", ["Timestamp", "User_ID"])
        with patch.dict(os.environ, {"GOOGLE_CREDS_B64": "configured"}), \
             patch("database.health.get_spreadsheet", return_value=FakeSpreadsheet([worksheet])):
            result = check_sheet_health()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(
            result["worksheets"]["SymptomLog"]["status"], "invalid_headers",
        )
        self.assertEqual(result["worksheets"]["Appointments"]["status"], "missing")

    def test_health_check_is_unavailable_without_credentials(self):
        from database.health import check_sheet_health

        with patch.dict(os.environ, {}, clear=True):
            result = check_sheet_health()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "credentials_missing")


if __name__ == "__main__":
    unittest.main()
