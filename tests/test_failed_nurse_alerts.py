# -*- coding: utf-8 -*-
"""Tests for failed nurse-alert persistence."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))


class FailedNurseAlertKeyTests(unittest.TestCase):

    def test_equivalent_payloads_generate_same_opaque_key(self):
        from database.failed_nurse_alerts import build_symptom_alert_idempotency_key

        key1 = build_symptom_alert_idempotency_key(
            user_id=" U-123 ",
            risk_code="HIGH",
            risk_score="3",
            pain=" 9 ",
            wound=" แผลปกติ ",
            fever="ไม่มี",
            mobility="เดินได้",
            neuro=None,
        )
        key2 = build_symptom_alert_idempotency_key(
            user_id="U-123",
            risk_code="high",
            risk_score=3,
            pain=9,
            wound="แผลปกติ",
            fever="ไม่มี",
            mobility="เดินได้",
            neuro="",
        )

        self.assertEqual(key1, key2)
        self.assertTrue(key1.startswith("symptom-alert:v1:"))
        self.assertNotIn("U-123", key1)
        self.assertNotIn("แผลปกติ", key1)

    def test_user_id_whitespace_normalizes_without_changing_identity(self):
        from database.failed_nurse_alerts import build_symptom_alert_idempotency_key

        key1 = build_symptom_alert_idempotency_key(" UserA ", "high", 3, 9, "ปกติ", "ไม่มี", "เดินได้", "")
        key2 = build_symptom_alert_idempotency_key("UserA", "high", 3, 9, "ปกติ", "ไม่มี", "เดินได้", "")

        self.assertEqual(key1, key2)

    def test_user_id_is_case_sensitive_opaque_identity(self):
        from database.failed_nurse_alerts import build_symptom_alert_idempotency_key

        key1 = build_symptom_alert_idempotency_key("UserA", "high", 3, 9, "ปกติ", "ไม่มี", "เดินได้", "")
        key2 = build_symptom_alert_idempotency_key("usera", "high", 3, 9, "ปกติ", "ไม่มี", "เดินได้", "")

        self.assertNotEqual(key1, key2)

    def test_meaningful_payload_change_changes_key(self):
        from database.failed_nurse_alerts import build_symptom_alert_idempotency_key

        base = build_symptom_alert_idempotency_key("U", "high", 3, 9, "ปกติ", "ไม่มี", "เดินได้", "")
        changed = build_symptom_alert_idempotency_key("U", "high", 3, 8, "ปกติ", "ไม่มี", "เดินได้", "")

        self.assertNotEqual(base, changed)


class FailedNurseAlertPersistenceTests(unittest.TestCase):

    def _call_save(self, **overrides):
        defaults = {
            "user_id": "U-123",
            "risk_code": "high",
            "risk_score": 3,
            "pain": "9",
            "wound": "ปกติ",
            "fever": "ไม่มี",
            "mobility": "เดินได้",
            "neuro": "",
            "notification_message": "แจ้งพยาบาล",
        }
        defaults.update(overrides)
        from database.failed_nurse_alerts import save_failed_symptom_alert
        return save_failed_symptom_alert(**defaults)

    def test_existing_worksheet_appends_one_documented_data_row(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.get_all_values.side_effect = AssertionError("must not scan backlog")
        with patch.object(failed, "get_worksheet", return_value=sheet), \
             patch.object(failed, "get_spreadsheet") as spreadsheet:
            result = self._call_save(notification_message="secretless alert")

        self.assertTrue(result)
        spreadsheet.assert_not_called()
        sheet.append_row.assert_called_once()
        row = sheet.append_row.call_args.args[0]
        self.assertEqual(len(row), len(failed.HEADER))
        self.assertEqual(row[2], "symptom_assessment")
        self.assertEqual(row[4], "high")
        self.assertEqual(row[5], 3)
        self.assertEqual(row[8], "pending")
        self.assertEqual(row[9], 0)
        self.assertEqual(row[10], "initial_line_push_failed")
        payload = json.loads(row[6])
        self.assertEqual(payload["risk_code"], "high")
        self.assertNotIn("Authorization", row[7])
        self.assertNotIn("Bearer", row[7])

    def test_persisted_user_identity_preserves_case_after_stripping(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        with patch.object(failed, "get_worksheet", return_value=sheet), \
             patch.object(failed, "get_spreadsheet"):
            result = self._call_save(user_id=" UserA ")

        self.assertTrue(result)
        row = sheet.append_row.call_args.args[0]
        payload = json.loads(row[6])
        self.assertEqual(row[3], "UserA")
        self.assertEqual(payload["user_id"], "UserA")

    def test_oversized_payload_remains_parseable_and_bounded(self):
        import database.failed_nurse_alerts as failed

        oversized = "x" * 10001 + "TAIL"
        sheet = Mock()
        with patch.object(failed, "get_worksheet", return_value=sheet), \
             patch.object(failed, "get_spreadsheet"):
            result = self._call_save(
                wound=oversized,
                fever=oversized,
                mobility=oversized,
                neuro=oversized,
            )

        self.assertTrue(result)
        row = sheet.append_row.call_args.args[0]
        payload_json = row[6]
        payload = json.loads(payload_json)
        self.assertLessEqual(len(payload_json), failed.PAYLOAD_JSON_MAX_CHARS)
        for key in ("user_id", "risk_code", "risk_score", "pain", "wound", "fever", "mobility", "neuro"):
            self.assertIn(key, payload)
        self.assertLessEqual(len(payload["wound"]), failed.WOUND_MAX_CHARS)
        self.assertLessEqual(len(payload["fever"]), failed.FEVER_MAX_CHARS)
        self.assertLessEqual(len(payload["mobility"]), failed.MOBILITY_MAX_CHARS)
        self.assertLessEqual(len(payload["neuro"]), failed.NEURO_MAX_CHARS)
        self.assertNotIn("TAIL", payload["wound"])
        self.assertNotIn("TAIL", payload["fever"])
        self.assertNotIn("TAIL", payload["mobility"])
        self.assertNotIn("TAIL", payload["neuro"])

    def test_stored_payload_and_key_use_same_bounded_identity(self):
        import database.failed_nurse_alerts as failed
        from database.failed_nurse_alerts import build_symptom_alert_idempotency_key

        oversized = "Y" * 10001 + "TAIL"
        sheet = Mock()
        with patch.object(failed, "get_worksheet", return_value=sheet), \
             patch.object(failed, "get_spreadsheet"):
            result = self._call_save(
                user_id=" UserA ",
                risk_code="HIGH",
                risk_score="3",
                pain=" 9 ",
                wound=oversized,
                fever=oversized,
                mobility=oversized,
                neuro=oversized,
            )

        self.assertTrue(result)
        row = sheet.append_row.call_args.args[0]
        payload = json.loads(row[6])
        expected_key = build_symptom_alert_idempotency_key(
            payload["user_id"],
            payload["risk_code"],
            payload["risk_score"],
            payload["pain"],
            payload["wound"],
            payload["fever"],
            payload["mobility"],
            payload["neuro"],
        )
        self.assertEqual(row[1], expected_key)

    def test_auto_create_path_writes_header_and_data_row(self):
        import database.failed_nurse_alerts as failed

        new_sheet = Mock()
        spreadsheet = Mock()
        spreadsheet.add_worksheet.return_value = new_sheet
        with patch.object(failed, "get_worksheet", return_value=None), \
             patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            result = self._call_save()

        self.assertTrue(result)
        spreadsheet.add_worksheet.assert_called_once()
        self.assertEqual(new_sheet.append_row.call_count, 2)
        self.assertEqual(new_sheet.append_row.call_args_list[0].args[0], failed.HEADER)
        data_row = new_sheet.append_row.call_args_list[1].args[0]
        self.assertEqual(data_row[2], "symptom_assessment")

    def test_data_row_append_failure_returns_false_without_raising(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.append_row.side_effect = RuntimeError("append failed")
        with patch.object(failed, "get_worksheet", return_value=sheet):
            result = self._call_save()

        self.assertFalse(result)

    def test_spreadsheet_unavailable_returns_false(self):
        import database.failed_nurse_alerts as failed

        with patch.object(failed, "get_worksheet", return_value=None), \
             patch.object(failed, "get_spreadsheet", return_value=None):
            result = self._call_save()

        self.assertFalse(result)


class FailedNurseAlertReadTests(unittest.TestCase):

    def test_read_existing_worksheet_returns_padded_records(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.get_all_values.return_value = [
            failed.HEADER,
            ["2026-06-20 09:00:00", "KEY1", "symptom_assessment", "U1", "high"],
        ]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["User_ID"], "U1")
        self.assertEqual(rows[0]["Risk_Level"], "high")
        self.assertEqual(rows[0]["Retry_Count"], "")
        spreadsheet.add_worksheet.assert_not_called()
        sheet.append_row.assert_not_called()
        sheet.update.assert_not_called()

    def test_read_future_extra_columns_preserved(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.get_all_values.return_value = [
            failed.HEADER + ["Future_Column"],
            ["2026-06-20 09:00:00", "KEY1", "symptom_assessment", "U1",
             "high", "3", "{}", "hidden", "pending", "0", "initial_line_push_failed", "future"],
        ]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertEqual(rows[0]["Future_Column"], "future")

    def test_missing_worksheet_returns_empty_without_create(self):
        import database.failed_nurse_alerts as failed

        class WorksheetNotFound(Exception):
            pass

        spreadsheet = Mock()
        spreadsheet.worksheet.side_effect = WorksheetNotFound()

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertEqual(rows, [])
        spreadsheet.add_worksheet.assert_not_called()

    def test_blank_header_returns_none(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.get_all_values.return_value = [["", "", ""], ["2026-06-20 09:00:00", "U1", "pending"]]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertIsNone(rows)
        spreadsheet.add_worksheet.assert_not_called()
        sheet.append_row.assert_not_called()

    def test_missing_required_status_header_returns_none(self):
        import database.failed_nurse_alerts as failed

        headers = [h for h in failed.HEADER if h != "Status"]
        sheet = Mock()
        sheet.get_all_values.return_value = [headers, ["2026-06-20 09:00:00", "KEY1"]]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertIsNone(rows)

    def test_missing_required_user_id_header_returns_none(self):
        import database.failed_nurse_alerts as failed

        headers = [h for h in failed.HEADER if h != "User_ID"]
        sheet = Mock()
        sheet.get_all_values.return_value = [headers, ["2026-06-20 09:00:00", "KEY1"]]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertIsNone(rows)

    def test_valid_header_only_returns_empty(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.get_all_values.return_value = [failed.HEADER]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertEqual(rows, [])

    def test_unavailable_spreadsheet_returns_none(self):
        import database.failed_nurse_alerts as failed

        with patch.object(failed, "get_spreadsheet", return_value=None):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertIsNone(rows)

    def test_read_exception_returns_none_without_raising(self):
        import database.failed_nurse_alerts as failed

        spreadsheet = Mock()
        spreadsheet.worksheet.side_effect = RuntimeError("sheet unavailable")

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            rows = failed.read_failed_nurse_alert_rows()

        self.assertIsNone(rows)
        spreadsheet.add_worksheet.assert_not_called()

    def test_read_never_uses_write_methods(self):
        import database.failed_nurse_alerts as failed

        sheet = Mock()
        sheet.get_all_values.return_value = [failed.HEADER]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = sheet

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet):
            failed.read_failed_nurse_alert_rows()

        spreadsheet.add_worksheet.assert_not_called()
        sheet.append_row.assert_not_called()
        sheet.update.assert_not_called()
        sheet.batch_update.assert_not_called()

    def test_read_error_log_does_not_include_payload_or_message_cells(self):
        import database.failed_nurse_alerts as failed

        spreadsheet = Mock()
        spreadsheet.worksheet.side_effect = RuntimeError("worksheet unavailable")

        with patch.object(failed, "get_spreadsheet", return_value=spreadsheet), \
             self.assertLogs("database.failed_nurse_alerts", level="ERROR") as logs:
            rows = failed.read_failed_nurse_alert_rows()

        self.assertIsNone(rows)
        joined = "\n".join(logs.output)
        self.assertNotIn("Payload_JSON", joined)
        self.assertNotIn("Notification_Message", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
