# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.patient_profile import is_valid_thai_citizen_id
from utils.parsers import parse_thai_colloquial_time, resolve_time_from_params
from services.llm import _parse_json_robust
from services.teleconsult import parse_category_choice


class UXAndRobustnessTest(unittest.TestCase):

    def test_thai_citizen_id_validation(self):
        self.assertTrue(is_valid_thai_citizen_id("1234567890121"))
        self.assertTrue(is_valid_thai_citizen_id(1234567890121))
        self.assertTrue(is_valid_thai_citizen_id(" 1-2345-67890-12-1 "))

        # Invalid Thai Citizen IDs
        self.assertFalse(is_valid_thai_citizen_id("1234567890123"))  # Wrong check digit
        self.assertFalse(is_valid_thai_citizen_id("1234"))           # Too short
        self.assertFalse(is_valid_thai_citizen_id("12345678901234")) # Too long
        self.assertFalse(is_valid_thai_citizen_id(None))
        self.assertFalse(is_valid_thai_citizen_id(""))

    def test_thai_colloquial_time_parsing(self):
        # Morning hours
        self.assertEqual(parse_thai_colloquial_time("เจ็ดโมงเช้า"), "07:00")
        self.assertEqual(parse_thai_colloquial_time("แปดโมง"), "08:00")
        self.assertEqual(parse_thai_colloquial_time("เก้าโมงเช้า"), "09:00")
        self.assertEqual(parse_thai_colloquial_time("สิบโมง"), "10:00")
        self.assertEqual(parse_thai_colloquial_time("สิบเอ็ดโมงครึ่ง"), "11:30")

        # Noon / Midnight
        self.assertEqual(parse_thai_colloquial_time("เที่ยง"), "12:00")
        self.assertEqual(parse_thai_colloquial_time("เที่ยงตรง"), "12:00")
        self.assertEqual(parse_thai_colloquial_time("เที่ยงคืน"), "00:00")

        # Afternoon / Evening
        self.assertEqual(parse_thai_colloquial_time("บ่ายโมง"), "13:00")
        self.assertEqual(parse_thai_colloquial_time("บ่ายสองโมง"), "14:00")
        self.assertEqual(parse_thai_colloquial_time("บ่ายสองโมงครึ่ง"), "14:30")
        self.assertEqual(parse_thai_colloquial_time("บ่าย 2 ครึ่ง"), "14:30")
        self.assertEqual(parse_thai_colloquial_time("สี่โมงเย็น"), "16:00")
        self.assertEqual(parse_thai_colloquial_time("ห้าโมงเย็นครึ่ง"), "17:30")
        self.assertEqual(parse_thai_colloquial_time("หกโมงเย็น"), "18:00")

        # Night hours
        self.assertEqual(parse_thai_colloquial_time("หนึ่งทุ่ม"), "19:00")
        self.assertEqual(parse_thai_colloquial_time("ทุ่มครึ่ง"), "19:30")
        self.assertEqual(parse_thai_colloquial_time("สองทุ่ม"), "20:00")
        self.assertEqual(parse_thai_colloquial_time("สี่ทุ่มครึ่ง"), "22:30")

        # Standard formats
        self.assertEqual(parse_thai_colloquial_time("14:30"), "14:30")
        self.assertEqual(parse_thai_colloquial_time("14.30น."), "14:30")

    def test_robust_json_parsing(self):
        # Standard valid JSON
        self.assertEqual(_parse_json_robust('{"key": "value"}'), {"key": "value"})

        # Markdown fenced JSON
        self.assertEqual(_parse_json_robust('```json\n{"key": "value"}\n```'), {"key": "value"})
        self.assertEqual(_parse_json_robust('```\n[1, 2, 3]\n```'), [1, 2, 3])

        # Extra leading/trailing text
        self.assertEqual(_parse_json_robust('Here is the JSON: {"key": "value"} hope you like it'), {"key": "value"})
        self.assertEqual(_parse_json_robust('Prefix text... [1, 2, 3] suffix text'), [1, 2, 3])

        # Invalid/Malformed JSON returns None instead of raising JSONDecodeError
        self.assertIsNone(_parse_json_robust('{"key": "value"'))
        self.assertIsNone(_parse_json_robust(None))

    def test_teleconsult_choice_parsing(self):
        # Direct string matching
        self.assertEqual(parse_category_choice("1"), "emergency")
        self.assertEqual(parse_category_choice("2"), "medication")
        self.assertEqual(parse_category_choice("3"), "wound")
        
        # Float matching (from Dialogflow parameter parsing)
        self.assertEqual(parse_category_choice(3.0), "wound")
        self.assertEqual(parse_category_choice("4.0"), "appointment")
        
        # Text matching
        self.assertEqual(parse_category_choice("แผลผ่าตัด"), "wound")
        self.assertEqual(parse_category_choice("ถามเรื่องยา"), "medication")


if __name__ == "__main__":
    unittest.main()
