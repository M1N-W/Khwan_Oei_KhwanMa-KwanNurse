# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["RUN_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PatientIdentityServiceTests(unittest.TestCase):

    def test_normalize_identity_fields_aliases_and_hn_uppercase(self):
        from services.patient_profile import normalize_identity_fields

        result = normalize_identity_fields({
            "patient_first_name": "  สมชาย  ",
            "family_name": "  ใจดี ",
            "hospital_number": " hn-001 ",
        })

        self.assertEqual(result, {
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "hn": "HN-001",
        })


class PatientIdentityWebhookTests(unittest.TestCase):

    def setUp(self):
        from flask import Flask
        self.app = Flask(__name__)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_handler_asks_for_missing_first_name(self):
        from routes.webhook import handle_patient_identity

        with patch("database.patient_profile.upsert_patient_profile"):
            response, status = handle_patient_identity("U1", {}, "")

        self.assertEqual(status, 200)
        self.assertIn("ชื่อจริง", response.get_json()["fulfillmentText"])

    def test_handler_merges_existing_profile_and_confirms(self):
        from routes.webhook import handle_patient_identity

        existing = {"age": 61, "sex": "f", "diseases": ["เบาหวาน"]}
        captured = {}

        def _upsert(user_id, profile):
            captured["user_id"] = user_id
            captured["profile"] = profile
            return True

        with patch("database.patient_profile.read_patient_profile", return_value=existing), \
             patch("database.patient_profile.upsert_patient_profile", side_effect=_upsert), \
             patch("services.dashboard_readers.invalidate_dashboard_cache", return_value=0):
            response, status = handle_patient_identity(
                "U1",
                {"first_name": "สมชาย", "last_name": "ใจดี", "hn": "hn001"},
                "สมชาย ใจดี hn001",
            )

        self.assertEqual(status, 200)
        self.assertIn("บันทึกข้อมูลคนไข้แล้ว", response.get_json()["fulfillmentText"])
        self.assertEqual(captured["user_id"], "U1")
        self.assertEqual(captured["profile"]["age"], 61)
        self.assertEqual(captured["profile"]["first_name"], "สมชาย")
        self.assertEqual(captured["profile"]["last_name"], "ใจดี")
        self.assertEqual(captured["profile"]["hn"], "HN001")


class DashboardIdentityTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_queue_item_contains_patient_label_from_profile(self):
        from services.dashboard_readers import QueueItem
        from datetime import datetime
        from config import LOCAL_TZ

        with patch("database.patient_profile.read_patient_profile", return_value={
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "hn": "HN001",
        }):
            item = QueueItem(
                queue_id="Q1",
                session_id="S1",
                user_id="U12345678901234567890",
                issue_type="wound",
                priority=2,
                status="waiting",
                waited_minutes=5,
                estimated_wait_minutes=20,
                queued_at=datetime(2026, 5, 2, 9, 0, 0, tzinfo=LOCAL_TZ),
            ).to_dict()

        self.assertEqual(item["patient_label"], "สมชาย ใจดี · HN HN001")
        self.assertEqual(item["patient_hn"], "HN001")

    def test_dashboard_identity_action_merges_existing_and_invalidates(self):
        from services.dashboard_actions import update_patient_identity

        captured = {}

        def _upsert(user_id, profile):
            captured["user_id"] = user_id
            captured["profile"] = profile
            return True

        with patch("database.patient_profile.read_patient_profile", return_value={"age": 70}), \
             patch("database.patient_profile.upsert_patient_profile", side_effect=_upsert), \
             patch("services.dashboard_actions.invalidate_dashboard_cache", return_value=0):
            result = update_patient_identity(
                "U1",
                "nurse_kwan",
                {"first_name": "สมหญิง", "last_name": "ดีมาก", "hn": "hn002"},
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured["profile"]["age"], 70)
        self.assertEqual(captured["profile"]["first_name"], "สมหญิง")
        self.assertEqual(captured["profile"]["last_name"], "ดีมาก")
        self.assertEqual(captured["profile"]["hn"], "HN002")


if __name__ == "__main__":
    unittest.main(verbosity=2)
