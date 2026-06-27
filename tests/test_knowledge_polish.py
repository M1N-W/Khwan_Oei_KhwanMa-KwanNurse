# -*- coding: utf-8 -*-
import unittest
from services import knowledge

class TestKnowledgePolish(unittest.TestCase):

    def test_knowledge_menu_formatting(self):
        menu = knowledge.get_knowledge_menu()
        self.assertIsNotNone(menu)
        # Verify divider length is short (less than 20 chars)
        for line in menu.split("\n"):
            if "──" in line:
                self.assertLessEqual(len(line), 20, f"Divider line too long: {line}")
            # Verify no hyphens used for bullet lists
            self.assertNotIn("   - ", line, "Hyphen bullet found in menu")
            # Verify clean white squares are used instead
            if any(item in line for item in ["การดูแลแผล", "กายภาพบำบัด", "ป้องกันลิ่มเลือด", "การรับประทานยา", "สัญญาณอันตราย"]):
                pass # Main numbers
            elif line.startswith("   "):
                self.assertIn("▫️", line, f"Expected ▫️ bullet in menu line: {line}")

    def test_wound_care_guide_formatting(self):
        guide = knowledge.get_wound_care_guide()
        self.assertIsNotNone(guide)
        for line in guide.split("\n"):
            if "──" in line:
                self.assertLessEqual(len(line), 20, f"Divider line too long: {line}")
            # Verify no old wide double-line dividers are left
            self.assertNotIn("══", line)
            # Verify line length constraints (less than 60 chars) to prevent mobile wrapping
            self.assertLessEqual(len(line), 60, f"Line too long: {line}")

    def test_physical_therapy_guide_formatting(self):
        guide = knowledge.get_physical_therapy_guide()
        self.assertIsNotNone(guide)
        for line in guide.split("\n"):
            if "──" in line:
                self.assertLessEqual(len(line), 20, f"Divider line too long: {line}")
            self.assertNotIn("══", line)
            self.assertLessEqual(len(line), 60, f"Line too long: {line}")

    def test_dvt_prevention_guide_formatting(self):
        guide = knowledge.get_dvt_prevention_guide()
        self.assertIsNotNone(guide)
        for line in guide.split("\n"):
            if "──" in line:
                self.assertLessEqual(len(line), 20, f"Divider line too long: {line}")
            self.assertNotIn("══", line)
            self.assertLessEqual(len(line), 60, f"Line too long: {line}")

    def test_medication_guide_formatting(self):
        guide = knowledge.get_medication_guide()
        self.assertIsNotNone(guide)
        for line in guide.split("\n"):
            if "──" in line:
                self.assertLessEqual(len(line), 20, f"Divider line too long: {line}")
            self.assertNotIn("══", line)
            self.assertLessEqual(len(line), 60, f"Line too long: {line}")

    def test_warning_signs_guide_formatting(self):
        guide = knowledge.get_warning_signs_guide()
        self.assertIsNotNone(guide)
        for line in guide.split("\n"):
            if "──" in line:
                self.assertLessEqual(len(line), 20, f"Divider line too long: {line}")
            self.assertNotIn("══", line)
            self.assertLessEqual(len(line), 60, f"Line too long: {line}")
