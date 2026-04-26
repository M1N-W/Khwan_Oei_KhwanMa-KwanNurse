# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 2 (S2-1): ทดสอบ pre-consult summary packet.

ขอบเขต:
- ``get_preconsult_packet`` returns None เมื่อ queue_id ไม่เจอ / empty.
- Cache hit/miss behavior + invalidation.
- Packet shape ครอบ key ที่ template ใช้.
- Helpers: ``_load_session_description``, ``_load_pending_reminders_safe``,
  ``_load_latest_risk_profile``, ``_build_briefing_safe`` ทนต่อ exception.
- Route ``GET /dashboard/queue/<id>/preview``: 200 ทุกกรณี (มี/ไม่มี packet),
  401 ถ้าไม่ได้ login, 404 ถ้า queue_id ผิด format.

Run::

    python -m unittest test_preconsult_summary.py -v
"""
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
# Dashboard auth ต้องตั้งค่าก่อน import app เพื่อให้ route เปิด
os.environ.setdefault(
    "NURSE_DASHBOARD_AUTH",
    # bcrypt hash ของ 'TestPass123!' (cost 4 — เร็วใน test)
    "test_nurse:$2b$04$DJTgqVFLGqYfBcCS9pInwOxR3oCPjPpLfXjWY55/lCNmL6emsIbzm",
)
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-for-preconsult-tests")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# Helper fakes
# -----------------------------------------------------------------------------
def _sample_queue_item(queue_id="Q-1", session_id="S-1", user_id="U-abc"):
    """รูปร่างเดียวกับ ``QueueItem.to_dict``."""
    return {
        "queue_id": queue_id,
        "session_id": session_id,
        "user_id": user_id,
        "user_id_short": "U-ab…",
        "issue_type": "wound",
        "priority": 2,
        "priority_label": "ปานกลาง",
        "status": "waiting",
        "waited_minutes": 8,
        "estimated_wait_minutes": 15,
        "queued_at": "10:00",
        "queued_at_full": "2026-04-26 10:00",
    }


def _sample_timeline(user_id="U-abc"):
    return {
        "user_id": user_id,
        "user_id_short": "U-ab…",
        "symptom_count": 2,
        "session_count": 1,
        "latest_risk_level": "medium",
        "events": [
            {
                "type": "symptom",
                "type_label": "รายงานอาการ",
                "timestamp": None,
                "timestamp_label": "26/04 09:30",
                "risk_level": "medium",
                "risk_score": 5,
                "pain": "ปานกลาง",
                "wound": "บวมเล็กน้อย",
                "fever": "ไม่มี",
                "mobility": "ดี",
            },
        ],
    }


# -----------------------------------------------------------------------------
# Packet shape + early returns
# -----------------------------------------------------------------------------
class PacketShapeTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_empty_queue_id_returns_none(self):
        from services.dashboard_readers import get_preconsult_packet
        self.assertIsNone(get_preconsult_packet(""))
        self.assertIsNone(get_preconsult_packet(None))

    def test_queue_not_found_returns_none(self):
        from services import dashboard_readers
        with patch.object(dashboard_readers, "get_queue_snapshot", return_value=[]):
            self.assertIsNone(dashboard_readers.get_preconsult_packet("Q-missing"))

    def test_packet_includes_all_template_keys(self):
        from services import dashboard_readers
        with patch.object(dashboard_readers, "get_queue_snapshot",
                          return_value=[_sample_queue_item()]), \
             patch.object(dashboard_readers, "get_patient_timeline",
                          return_value=_sample_timeline()), \
             patch.object(dashboard_readers, "_load_session_description",
                          return_value="แผลแดงและร้อนเล็กน้อย"), \
             patch.object(dashboard_readers, "_load_pending_reminders_safe",
                          return_value=[{"reminder_type": "day3", "scheduled_for": "2026-04-27", "status": "sent"}]), \
             patch.object(dashboard_readers, "_load_latest_risk_profile",
                          return_value={"age": "58", "sex": "F", "bmi": "26",
                                        "diseases": "diabetes",
                                        "risk_level": "medium",
                                        "timestamp": "2026-04-20"}), \
             patch.object(dashboard_readers, "_build_briefing_safe",
                          return_value="สรุปสั้น ๆ"):
            packet = dashboard_readers.get_preconsult_packet("Q-1")

        self.assertIsNotNone(packet)
        # Keys ที่ template ใช้
        for key in (
            "queue_id", "session_id", "user_id", "user_id_short",
            "issue_type", "issue_label", "priority", "priority_label",
            "queued_at", "waited_minutes", "description",
            "latest_risk_level", "symptom_count", "session_count",
            "recent_events", "pending_reminders", "risk_profile", "briefing",
        ):
            self.assertIn(key, packet, f"missing key: {key}")
        self.assertEqual(packet["queue_id"], "Q-1")
        self.assertEqual(packet["user_id"], "U-abc")
        self.assertEqual(packet["description"], "แผลแดงและร้อนเล็กน้อย")
        self.assertEqual(packet["issue_label"], "แผลผ่าตัด")  # จาก ISSUE_CATEGORIES['wound']
        self.assertEqual(len(packet["recent_events"]), 1)

    def test_packet_truncates_description_to_500_chars(self):
        from services import dashboard_readers
        long_desc = "ก" * 1000
        with patch.object(dashboard_readers, "get_queue_snapshot",
                          return_value=[_sample_queue_item()]), \
             patch.object(dashboard_readers, "get_patient_timeline",
                          return_value=_sample_timeline()), \
             patch.object(dashboard_readers, "_load_session_description",
                          return_value=long_desc), \
             patch.object(dashboard_readers, "_load_pending_reminders_safe",
                          return_value=[]), \
             patch.object(dashboard_readers, "_load_latest_risk_profile",
                          return_value=None), \
             patch.object(dashboard_readers, "_build_briefing_safe",
                          return_value=""):
            packet = dashboard_readers.get_preconsult_packet("Q-1")
        self.assertEqual(len(packet["description"]), 500)


# -----------------------------------------------------------------------------
# Cache behavior
# -----------------------------------------------------------------------------
class CacheBehaviorTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_second_call_hits_cache(self):
        from services import dashboard_readers
        with patch.object(dashboard_readers, "get_queue_snapshot",
                          return_value=[_sample_queue_item()]) as mock_q, \
             patch.object(dashboard_readers, "get_patient_timeline",
                          return_value=_sample_timeline()), \
             patch.object(dashboard_readers, "_load_session_description",
                          return_value=""), \
             patch.object(dashboard_readers, "_load_pending_reminders_safe",
                          return_value=[]), \
             patch.object(dashboard_readers, "_load_latest_risk_profile",
                          return_value=None), \
             patch.object(dashboard_readers, "_build_briefing_safe",
                          return_value=""):
            dashboard_readers.get_preconsult_packet("Q-1")
            dashboard_readers.get_preconsult_packet("Q-1")
            # Snapshot ถูกเรียกแค่ครั้งแรก — ครั้งที่สอง hit cache
            self.assertEqual(mock_q.call_count, 1)

    def test_force_refresh_skips_cache(self):
        from services import dashboard_readers
        with patch.object(dashboard_readers, "get_queue_snapshot",
                          return_value=[_sample_queue_item()]) as mock_q, \
             patch.object(dashboard_readers, "get_patient_timeline",
                          return_value=_sample_timeline()), \
             patch.object(dashboard_readers, "_load_session_description",
                          return_value=""), \
             patch.object(dashboard_readers, "_load_pending_reminders_safe",
                          return_value=[]), \
             patch.object(dashboard_readers, "_load_latest_risk_profile",
                          return_value=None), \
             patch.object(dashboard_readers, "_build_briefing_safe",
                          return_value=""):
            dashboard_readers.get_preconsult_packet("Q-1")
            dashboard_readers.get_preconsult_packet("Q-1", force_refresh=True)
            self.assertEqual(mock_q.call_count, 2)

    def test_invalidate_dashboard_cache_clears_preconsult(self):
        from services import dashboard_readers
        from services.cache import ttl_cache
        ttl_cache.set("dash:preconsult:v1:Q-1", {"x": 1}, 60)
        self.assertIsNotNone(ttl_cache.get("dash:preconsult:v1:Q-1"))
        dashboard_readers.invalidate_dashboard_cache()
        self.assertIsNone(ttl_cache.get("dash:preconsult:v1:Q-1"))


# -----------------------------------------------------------------------------
# Helper resilience
# -----------------------------------------------------------------------------
class HelperResilienceTests(unittest.TestCase):

    def test_load_session_description_handles_missing_session(self):
        from services import dashboard_readers

        class _Sheet:
            def get_all_values(self):
                return [
                    ["Session_ID", "Timestamp", "User_ID", "Issue_Type",
                     "Priority", "Status", "Description"],
                    ["S-other", "ts", "U-x", "wound", "2", "queued", "x"],
                ]

        with patch("database.sheets.get_worksheet", return_value=_Sheet()):
            self.assertEqual(dashboard_readers._load_session_description("S-missing"), "")

    def test_load_session_description_returns_value_when_found(self):
        from services import dashboard_readers

        class _Sheet:
            def get_all_values(self):
                return [
                    ["Session_ID", "Timestamp", "User_ID", "Issue_Type",
                     "Priority", "Status", "Description"],
                    ["S-1", "ts", "U-x", "wound", "2", "queued", "เจ็บมาก"],
                ]

        with patch("database.sheets.get_worksheet", return_value=_Sheet()):
            self.assertEqual(dashboard_readers._load_session_description("S-1"), "เจ็บมาก")

    def test_load_pending_reminders_swallows_exception(self):
        from services import dashboard_readers
        with patch("database.reminders.get_pending_reminders",
                   side_effect=RuntimeError("boom")):
            self.assertEqual(dashboard_readers._load_pending_reminders_safe("U-x"), [])

    def test_build_briefing_safe_swallows_exception(self):
        from services import dashboard_readers
        with patch("services.presession.build_pre_consult_briefing",
                   side_effect=RuntimeError("boom")):
            self.assertEqual(dashboard_readers._build_briefing_safe("U-x", "wound", "desc"), "")

    def test_load_latest_risk_profile_finds_latest_row(self):
        from services import dashboard_readers

        class _Sheet:
            def get_all_values(self):
                return [
                    ["Timestamp", "User_ID", "Age", "Sex", "BMI", "Diseases", "Risk_Level"],
                    ["2026-04-01", "U-x", "55", "F", "24", "-", "low"],
                    ["2026-04-20", "U-x", "58", "F", "26", "diabetes", "medium"],
                ]

        with patch("database.sheets.get_worksheet", return_value=_Sheet()):
            profile = dashboard_readers._load_latest_risk_profile("U-x")
        self.assertEqual(profile["age"], "58")
        self.assertEqual(profile["risk_level"], "medium")


# -----------------------------------------------------------------------------
# Route smoke tests
# -----------------------------------------------------------------------------
class PreviewRouteTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        # Login เป็น nurse — ใช้ helper จาก test_dashboard_auth ไม่ได้เพราะ
        # มันสร้าง app ตัวเอง. ทำเองด้วย session transaction.
        import time
        with self.client.session_transaction() as sess:
            sess["nurse_user"] = "test_nurse"
            sess["nurse_last_active"] = time.time()
            sess["nurse_csrf"] = "test-csrf"

    def test_preview_route_requires_auth(self):
        # ใช้ client ใหม่ ไม่มี session
        client = self.app.test_client()
        resp = client.get("/dashboard/queue/Q-1/preview", follow_redirects=False)
        self.assertIn(resp.status_code, (302, 401))

    def test_preview_renders_packet_when_found(self):
        # Patch ที่ namespace ของ view ที่ import มาแล้ว ไม่ใช่ที่ source module
        from routes.dashboard import views as dashboard_views
        with patch.object(dashboard_views, "get_preconsult_packet",
                          return_value={
                              "queue_id": "Q-1",
                              "session_id": "S-1",
                              "user_id": "U-abc",
                              "user_id_short": "U-ab…",
                              "issue_type": "wound",
                              "issue_label": "แผลผ่าตัด",
                              "priority": 2,
                              "priority_label": "ปานกลาง",
                              "queued_at": "2026-04-26 10:00",
                              "waited_minutes": 8,
                              "description": "เจ็บมาก",
                              "latest_risk_level": "medium",
                              "symptom_count": 2,
                              "session_count": 1,
                              "recent_events": [],
                              "pending_reminders": [],
                              "risk_profile": None,
                              "briefing": "",
                          }):
            resp = self.client.get("/dashboard/queue/Q-1/preview")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("สรุปก่อนรับเคส", body)
        self.assertIn("Q-1", body)
        self.assertIn("เจ็บมาก", body)

    def test_preview_renders_not_found_when_packet_none(self):
        from routes.dashboard import views as dashboard_views
        with patch.object(dashboard_views, "get_preconsult_packet",
                          return_value=None):
            resp = self.client.get("/dashboard/queue/Q-missing/preview")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ไม่พบข้อมูลคิวนี้", resp.get_data(as_text=True))

    def test_preview_404_for_oversized_id(self):
        resp = self.client.get("/dashboard/queue/" + ("x" * 100) + "/preview")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
