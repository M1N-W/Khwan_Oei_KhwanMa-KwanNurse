# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 2 (S2-3): ทดสอบ Personalized Education end-to-end.

ขอบเขต:
- ``database.patient_profile``: read row + upsert (insert + update path).
- ``services.patient_profile.get_or_build_profile``: merge precedence,
  cache, persist of new sticky fields, RiskProfile fallback.
- ``services.education.recommend_guides``: ลำดับ rule-based ให้ผลตาม
  profile ที่ provide จาก patient_profile (smoke).
- Webhook integration: ``handle_recommend_knowledge`` ใช้ stored profile
  แม้ Dialogflow params ไม่มี profile field.

Run::

    python -m unittest test_personalized_education.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# database/patient_profile
# -----------------------------------------------------------------------------
class PatientProfileSheetTests(unittest.TestCase):

    def test_read_returns_none_when_sheet_unavailable(self):
        from database import patient_profile as pp_db
        with patch.object(pp_db, "get_worksheet", return_value=None):
            self.assertIsNone(pp_db.read_patient_profile("U-x"))

    def test_read_returns_none_when_user_not_found(self):
        from database import patient_profile as pp_db

        class _Sheet:
            def get_all_values(self):
                return [
                    pp_db.HEADERS,
                    ["U-other", "60", "f", "knee_replacement", "", "", "2025-01-01 09:00:00"],
                ]

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            self.assertIsNone(pp_db.read_patient_profile("U-x"))

    def test_read_returns_normalized_dict(self):
        from database import patient_profile as pp_db

        class _Sheet:
            def get_all_values(self):
                return [
                    pp_db.HEADERS,
                    ["U-x", "60", "F", "Knee_Replacement", "2025-04-01",
                     "เบาหวาน, ความดัน", "2025-04-15 09:00:00"],
                ]

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            rec = pp_db.read_patient_profile("U-x")
        self.assertEqual(rec["age"], 60)
        self.assertEqual(rec["sex"], "f")  # lowercased
        self.assertEqual(rec["surgery_type"], "knee_replacement")
        self.assertEqual(rec["surgery_date"], "2025-04-01")
        self.assertEqual(rec["diseases"], ["เบาหวาน", "ความดัน"])
        self.assertIsNone(rec["first_name"])
        self.assertIsNone(rec["last_name"])
        self.assertIsNone(rec["hn"])

    def test_read_returns_identity_fields_from_new_schema(self):
        from database import patient_profile as pp_db

        class _Sheet:
            def get_all_values(self):
                return [
                    pp_db.HEADERS,
                    ["U-x", "60", "F", "Knee_Replacement", "2025-04-01",
                     "เบาหวาน", "2025-04-15 09:00:00", "สมชาย", "ใจดี", "HN001"],
                ]

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            rec = pp_db.read_patient_profile("U-x")
        self.assertEqual(rec["first_name"], "สมชาย")
        self.assertEqual(rec["last_name"], "ใจดี")
        self.assertEqual(rec["hn"], "HN001")
        self.assertEqual(rec["display_label"], "สมชาย ใจดี · HN HN001")

    def test_upsert_appends_when_user_missing(self):
        from database import patient_profile as pp_db
        captured = {}

        class _Sheet:
            def get_all_values(self):
                return [pp_db.HEADERS]  # only headers, empty data

            def append_row(self, row, value_input_option=None):
                captured["row"] = row

            def update(self, *_a, **_kw):
                captured["updated"] = True

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U-new", {
                "age": 65, "sex": "M", "surgery_type": "hip",
                "diseases": ["เบาหวาน"], "first_name": "สมชาย",
                "last_name": "ใจดี", "hn": "HN001",
            })
        self.assertTrue(ok)
        self.assertNotIn("updated", captured)
        self.assertEqual(captured["row"][0], "U-new")
        self.assertEqual(captured["row"][1], "65")
        self.assertEqual(captured["row"][2], "m")
        self.assertEqual(captured["row"][3], "hip")
        self.assertIn("เบาหวาน", captured["row"][5])
        self.assertEqual(captured["row"][7], "สมชาย")
        self.assertEqual(captured["row"][8], "ใจดี")
        self.assertEqual(captured["row"][9], "HN001")

    def test_upsert_updates_existing_row(self):
        from database import patient_profile as pp_db
        captured = {}

        class _Sheet:
            def get_all_values(self):
                return [
                    pp_db.HEADERS,
                    ["U-other", "70", "f", "knee", "", "", "2025-01-01"],
                    ["U-x", "60", "f", "knee", "", "", "2025-01-01"],
                ]

            def append_row(self, *_a, **_kw):
                captured["appended"] = True

            def update(self, target_range, values, value_input_option=None):
                captured["range"] = target_range
                captured["values"] = values

        with patch.object(pp_db, "get_worksheet", return_value=_Sheet()):
            ok = pp_db.upsert_patient_profile("U-x", {
                "age": 61, "sex": "f", "surgery_type": "knee_replacement",
            })
        self.assertTrue(ok)
        self.assertNotIn("appended", captured)
        # Sheet row 3 = data row 2 (1-indexed; row 1 is headers)
        self.assertEqual(captured["range"], "A3:J3")
        # Surgery_Type col (index 3) updated
        self.assertEqual(captured["values"][0][3], "knee_replacement")


# -----------------------------------------------------------------------------
# services/patient_profile
# -----------------------------------------------------------------------------
class GetOrBuildProfileTests(unittest.TestCase):

    def setUp(self):
        # Always start with a clean cache so test order doesn't matter.
        from services import patient_profile as pp_svc
        pp_svc.invalidate_profile_cache()

    def test_anonymous_user_returns_override_only(self):
        from services.patient_profile import get_or_build_profile
        result = get_or_build_profile("", {"age": 60, "surgery_type": "hip"})
        self.assertEqual(result["age"], 60)
        self.assertEqual(result["surgery_type"], "hip")
        self.assertEqual(result["source"], "override")

    def test_override_wins_over_stored(self):
        from services import patient_profile as pp_svc
        with patch.object(pp_svc, "read_patient_profile",
                          return_value={"age": 70, "sex": "m",
                                        "surgery_type": "knee", "diseases": []}), \
             patch.object(pp_svc, "_load_latest_risk", return_value={}), \
             patch.object(pp_svc, "upsert_patient_profile", return_value=True):
            result = pp_svc.get_or_build_profile(
                "U-x", {"age": 72, "surgery_type": "hip"},
            )
        self.assertEqual(result["age"], 72)
        self.assertEqual(result["surgery_type"], "hip")
        self.assertEqual(result["sex"], "m")  # came from stored
        self.assertIn("override", result["source"])

    def test_risk_profile_fallback_when_no_stored(self):
        from services import patient_profile as pp_svc
        with patch.object(pp_svc, "read_patient_profile", return_value=None), \
             patch.object(pp_svc, "_load_latest_risk",
                          return_value={"age": 58, "diseases": ["เบาหวาน"]}), \
             patch.object(pp_svc, "upsert_patient_profile", return_value=True):
            result = pp_svc.get_or_build_profile("U-x", {})
        self.assertEqual(result["age"], 58)
        self.assertEqual(result["diseases"], ["เบาหวาน"])
        self.assertNotIn("surgery_type", result)
        self.assertIn("risk", result["source"])

    def test_new_sticky_field_triggers_upsert(self):
        from services import patient_profile as pp_svc
        with patch.object(pp_svc, "read_patient_profile", return_value=None), \
             patch.object(pp_svc, "_load_latest_risk",
                          return_value={"age": 58, "diseases": []}), \
             patch.object(pp_svc, "upsert_patient_profile",
                          return_value=True) as mock_upsert:
            pp_svc.get_or_build_profile("U-x", {"surgery_type": "hip", "sex": "f"})
        mock_upsert.assert_called_once()
        written = mock_upsert.call_args[0][1]
        self.assertEqual(written["surgery_type"], "hip")
        self.assertEqual(written["sex"], "f")

    def test_no_sticky_change_skips_upsert(self):
        from services import patient_profile as pp_svc
        stored = {"age": 60, "sex": "m", "surgery_type": "knee",
                  "surgery_date": None, "diseases": []}
        with patch.object(pp_svc, "read_patient_profile", return_value=stored), \
             patch.object(pp_svc, "_load_latest_risk", return_value={}), \
             patch.object(pp_svc, "upsert_patient_profile",
                          return_value=True) as mock_upsert:
            pp_svc.get_or_build_profile("U-x", {})  # no override at all
        mock_upsert.assert_not_called()

    def test_persist_false_disables_upsert(self):
        from services import patient_profile as pp_svc
        with patch.object(pp_svc, "read_patient_profile", return_value=None), \
             patch.object(pp_svc, "_load_latest_risk", return_value={}), \
             patch.object(pp_svc, "upsert_patient_profile",
                          return_value=True) as mock_upsert:
            pp_svc.get_or_build_profile(
                "U-x", {"surgery_type": "hip"}, persist=False,
            )
        mock_upsert.assert_not_called()

    def test_cache_hit_skips_sheet_reads(self):
        from services import patient_profile as pp_svc
        with patch.object(pp_svc, "read_patient_profile", return_value=None) as mock_read, \
             patch.object(pp_svc, "_load_latest_risk",
                          return_value={"age": 60, "diseases": []}), \
             patch.object(pp_svc, "upsert_patient_profile", return_value=True):
            pp_svc.get_or_build_profile("U-cached", {})
            pp_svc.get_or_build_profile("U-cached", {})  # 2nd call hits cache
        # read_patient_profile only called once (first call)
        self.assertEqual(mock_read.call_count, 1)


# -----------------------------------------------------------------------------
# Education recommender — smoke that profile shape from get_or_build_profile
# is consumed correctly by recommend_guides.
# -----------------------------------------------------------------------------
class RecommenderIntegrationTests(unittest.TestCase):

    def test_orthopedic_elderly_profile_promotes_pt_dvt(self):
        from services.education import recommend_guides
        recs = recommend_guides({
            "age": 72,
            "sex": "f",
            "surgery_type": "hip_replacement",
            "diseases": ["เบาหวาน"],
        }, top_n=5)
        keys = [r["key"] for r in recs]
        self.assertEqual(keys[0], "wound_care")
        # PT and DVT should appear in top 4 for ortho/elderly
        self.assertIn("physical_therapy", keys[:4])
        self.assertIn("dvt_prevention", keys[:4])


# -----------------------------------------------------------------------------
# /webhook RecommendKnowledge integration
# -----------------------------------------------------------------------------
class RecommendKnowledgeRouteTests(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("FLASK_SECRET_KEY",
                              "test-secret-key-for-personalized-education-tests")
        from services import patient_profile as pp_svc
        pp_svc.invalidate_profile_cache()

        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _post_recommend(self, params=None):
        return self.client.post("/webhook", json={
            "queryResult": {
                "intent": {"displayName": "RecommendKnowledge"},
                "parameters": params or {},
                "queryText": "แนะนำความรู้",
            },
            "session": "projects/x/agent/sessions/U-route-test-12345",
        })

    def test_uses_stored_profile_when_dialogflow_params_empty(self):
        from services import patient_profile as pp_svc
        stored = {"age": 65, "sex": "m", "surgery_type": "knee_replacement",
                  "surgery_date": None, "diseases": ["เบาหวาน"]}
        with patch.object(pp_svc, "read_patient_profile", return_value=stored), \
             patch.object(pp_svc, "_load_latest_risk", return_value={}), \
             patch.object(pp_svc, "upsert_patient_profile", return_value=True):
            resp = self._post_recommend({})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()["fulfillmentText"]
        # Top recommendation always wound care; ortho profile boosts PT.
        self.assertIn("ดูแลแผล", body)

    def test_dialogflow_params_override_stored(self):
        from services import patient_profile as pp_svc
        stored = {"age": 30, "sex": "m", "surgery_type": "general",
                  "surgery_date": None, "diseases": []}
        captured = {}

        original_recommend = None
        from services import education

        def _spy(profile, top_n=3):
            captured["profile"] = profile
            return original_recommend(profile, top_n=top_n)

        original_recommend = education.recommend_guides

        with patch.object(pp_svc, "read_patient_profile", return_value=stored), \
             patch.object(pp_svc, "_load_latest_risk", return_value={}), \
             patch.object(pp_svc, "upsert_patient_profile", return_value=True), \
             patch("routes.webhook.recommend_guides", side_effect=_spy):
            resp = self._post_recommend({"age": 75, "surgery_type": "hip"})
        self.assertEqual(resp.status_code, 200)
        # Override age + surgery should beat stored values
        self.assertEqual(captured["profile"]["age"], 75)
        self.assertEqual(captured["profile"]["surgery_type"], "hip")


if __name__ == "__main__":
    unittest.main()
