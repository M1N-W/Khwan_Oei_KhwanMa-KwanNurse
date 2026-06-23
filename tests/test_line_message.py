# -*- coding: utf-8 -*-
"""
Tests for KWN-05: LINE Message Delivery Layer.
4-Tier methodology.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("NURSE_GROUP_ID", "test_nurse_group")


import services.line_message as lm


class TestBuildTextMessage(unittest.TestCase):
    """Tier 1 + 2: build_text_message"""

    def test_basic_text(self):
        msg = lm.build_text_message("สวัสดี")
        self.assertEqual(msg["type"], "text")
        self.assertEqual(msg["text"], "สวัสดี")

    def test_empty_string(self):
        msg = lm.build_text_message("")
        self.assertEqual(msg["text"], "")

    def test_non_string_coerced(self):
        msg = lm.build_text_message(12345)
        self.assertIsInstance(msg["text"], str)

    def test_at_limit_not_truncated(self):
        text = "A" * lm.MAX_TEXT_CHARS
        msg = lm.build_text_message(text)
        self.assertEqual(len(msg["text"]), lm.MAX_TEXT_CHARS)

    def test_over_limit_truncated(self):
        text = "B" * (lm.MAX_TEXT_CHARS + 100)
        msg = lm.build_text_message(text)
        self.assertEqual(len(msg["text"]), lm.MAX_TEXT_CHARS)

    def test_unicode_thai(self):
        text = "ขอบคุณ" * 100
        msg = lm.build_text_message(text)
        self.assertIn("ขอบคุณ", msg["text"])


class TestQuickReplyBuilders(unittest.TestCase):
    """Tier 1 + 2: quick_reply_item, quick_reply_postback, build_quick_reply_message"""

    def test_quick_reply_item_structure(self):
        item = lm.quick_reply_item("ใช่", "ใช่")
        self.assertEqual(item["type"], "action")
        self.assertEqual(item["action"]["type"], "message")
        self.assertEqual(item["action"]["label"], "ใช่")
        self.assertEqual(item["action"]["text"], "ใช่")

    def test_quick_reply_item_label_truncated(self):
        label = "A" * 25
        item = lm.quick_reply_item(label, "ok")
        self.assertLessEqual(len(item["action"]["label"]), 20)

    def test_quick_reply_item_with_image(self):
        item = lm.quick_reply_item("icon", "text", image_url="https://example.com/icon.png")
        self.assertEqual(item["imageUrl"], "https://example.com/icon.png")

    def test_quick_reply_postback_structure(self):
        item = lm.quick_reply_postback("ยืนยัน", "action=confirm")
        self.assertEqual(item["action"]["type"], "postback")
        self.assertEqual(item["action"]["data"], "action=confirm")

    def test_quick_reply_postback_with_display_text(self):
        item = lm.quick_reply_postback("ยืนยัน", "action=confirm", display_text="ยืนยันแล้ว")
        self.assertEqual(item["action"]["displayText"], "ยืนยันแล้ว")

    def test_build_quick_reply_message_structure(self):
        items = [lm.quick_reply_item("ใช่", "ใช่"), lm.quick_reply_item("ไม่", "ไม่")]
        msg = lm.build_quick_reply_message("เลือกคำตอบ", items)
        self.assertEqual(msg["type"], "text")
        self.assertIn("quickReply", msg)
        self.assertEqual(len(msg["quickReply"]["items"]), 2)

    def test_quick_reply_capped_at_max(self):
        items = [lm.quick_reply_item(f"opt{i}", f"opt{i}") for i in range(20)]
        msg = lm.build_quick_reply_message("choose", items)
        self.assertEqual(len(msg["quickReply"]["items"]), lm.MAX_QUICK_REPLY_ITEMS)

    def test_quick_reply_empty_items(self):
        msg = lm.build_quick_reply_message("text", [])
        self.assertEqual(msg["quickReply"]["items"], [])


class TestFlexBuilders(unittest.TestCase):
    """Tier 1 + 2: flex_text, flex_button, flex_bubble, build_flex_message"""

    def test_flex_text_defaults(self):
        comp = lm.flex_text("Hello")
        self.assertEqual(comp["type"], "text")
        self.assertEqual(comp["text"], "Hello")
        self.assertEqual(comp["weight"], "regular")
        self.assertEqual(comp["size"], "md")
        self.assertTrue(comp["wrap"])

    def test_flex_text_custom(self):
        comp = lm.flex_text("Bold", weight="bold", size="xl", color="#FF0000")
        self.assertEqual(comp["weight"], "bold")
        self.assertEqual(comp["color"], "#FF0000")

    def test_flex_button_message(self):
        btn = lm.flex_button("กด", action_type="message", action_text="กดแล้ว")
        self.assertEqual(btn["type"], "button")
        self.assertEqual(btn["action"]["type"], "message")
        self.assertEqual(btn["action"]["text"], "กดแล้ว")

    def test_flex_button_uri(self):
        btn = lm.flex_button("เปิด", action_type="uri", action_uri="https://example.com")
        self.assertEqual(btn["action"]["type"], "uri")
        self.assertEqual(btn["action"]["uri"], "https://example.com")

    def test_flex_separator(self):
        sep = lm.flex_separator()
        self.assertEqual(sep["type"], "separator")

    def test_flex_bubble_minimal(self):
        bubble = lm.flex_bubble([lm.flex_text("Body")])
        self.assertEqual(bubble["type"], "bubble")
        self.assertIn("body", bubble)
        self.assertNotIn("header", bubble)

    def test_flex_bubble_with_header(self):
        bubble = lm.flex_bubble([lm.flex_text("Body")], header_text="หัวข้อ")
        self.assertIn("header", bubble)
        self.assertEqual(bubble["header"]["contents"][0]["text"], "หัวข้อ")

    def test_flex_bubble_with_footer(self):
        bubble = lm.flex_bubble(
            [lm.flex_text("Body")],
            footer_components=[lm.flex_button("ปิด", action_text="ปิด")]
        )
        self.assertIn("footer", bubble)

    def test_build_flex_message_structure(self):
        bubble = lm.flex_bubble([lm.flex_text("Hello")])
        msg = lm.build_flex_message("alt text", bubble)
        self.assertEqual(msg["type"], "flex")
        self.assertEqual(msg["altText"], "alt text")
        self.assertIn("contents", msg)

    def test_build_flex_alt_text_truncated(self):
        long_alt = "X" * 500
        msg = lm.build_flex_message(long_alt, {})
        self.assertLessEqual(len(msg["altText"]), lm.MAX_FLEX_ALT_TEXT_CHARS)


class TestValidateLinePayload(unittest.TestCase):
    """Tier 2: validate_line_payload edge cases"""

    def test_empty_list_invalid(self):
        ok, reason = lm.validate_line_payload([])
        self.assertFalse(ok)
        self.assertIn("empty", reason)

    def test_too_many_messages(self):
        msgs = [lm.build_text_message("x")] * (lm.MAX_MESSAGES_PER_CALL + 1)
        ok, reason = lm.validate_line_payload(msgs)
        self.assertFalse(ok)
        self.assertIn("too many", reason)

    def test_valid_single_text(self):
        ok, _ = lm.validate_line_payload([lm.build_text_message("hello")])
        self.assertTrue(ok)

    def test_invalid_message_type(self):
        ok, reason = lm.validate_line_payload([{"type": "unknown_type"}])
        self.assertFalse(ok)

    def test_flex_missing_alt_text(self):
        ok, reason = lm.validate_line_payload([{"type": "flex", "contents": {}}])
        self.assertFalse(ok)
        self.assertIn("altText", reason)

    def test_flex_missing_contents(self):
        ok, reason = lm.validate_line_payload([{"type": "flex", "altText": "alt"}])
        self.assertFalse(ok)
        self.assertIn("contents", reason)

    def test_quick_reply_over_limit_flagged(self):
        items = [lm.quick_reply_item(f"x{i}", f"x{i}") for i in range(20)]
        # Manually bypass builder cap to test validator
        msg = lm.build_text_message("choose")
        msg["quickReply"] = {"items": items}
        ok, reason = lm.validate_line_payload([msg])
        self.assertFalse(ok)
        self.assertIn("quickReply", reason)


class TestSendHelpersFallback(unittest.TestCase):
    """Tier 3: Feature flag fallback behaviour"""

    @patch("services.line_message.ENABLE_RICH_MESSAGES", False)
    @patch("services.notification.send_line_push")
    def test_push_rich_falls_back_to_text(self, mock_push):
        mock_push.return_value = True
        msgs = [lm.build_text_message("Hello")]
        result = lm.push_rich_message(msgs, "U123")
        mock_push.assert_called_once_with("Hello", "U123")
        self.assertTrue(result)

    @patch("services.line_message.ENABLE_RICH_MESSAGES", False)
    @patch("services.notification.reply_line_message")
    def test_reply_rich_falls_back_to_text(self, mock_reply):
        mock_reply.return_value = True
        msgs = [lm.build_text_message("Reply text")]
        result = lm.reply_rich_message("TOKEN123", msgs)
        mock_reply.assert_called_once_with("TOKEN123", "Reply text")
        self.assertTrue(result)

    @patch("services.line_message.ENABLE_RICH_MESSAGES", True)
    @patch("services.notification.send_line_push_objects")
    def test_push_rich_enabled_calls_objects(self, mock_push_obj):
        mock_push_obj.return_value = True
        msgs = [lm.build_text_message("Hello")]
        result = lm.push_rich_message(msgs, "U123")
        mock_push_obj.assert_called_once_with(msgs, "U123")
        self.assertTrue(result)

    @patch("services.line_message.ENABLE_RICH_MESSAGES", True)
    @patch("services.notification.reply_line_message_objects")
    def test_reply_rich_enabled_calls_objects(self, mock_reply_obj):
        mock_reply_obj.return_value = True
        msgs = [lm.build_text_message("Hello")]
        result = lm.reply_rich_message("TOKEN123", msgs)
        mock_reply_obj.assert_called_once_with("TOKEN123", msgs)
        self.assertTrue(result)

    @patch("services.line_message.ENABLE_RICH_MESSAGES", True)
    def test_push_rich_invalid_payload_returns_false(self):
        result = lm.push_rich_message([], "U123")
        self.assertFalse(result)

    def test_push_rich_missing_target_returns_false(self):
        result = lm.push_rich_message([lm.build_text_message("x")], "")
        self.assertFalse(result)

    @patch("services.line_message.ENABLE_RICH_MESSAGES", False)
    @patch("services.notification.send_line_push")
    def test_flex_fallback_uses_alt_text(self, mock_push):
        mock_push.return_value = True
        bubble = lm.flex_bubble([lm.flex_text("body")])
        msgs = [lm.build_flex_message("alt fallback", bubble)]
        lm.push_rich_message(msgs, "U123")
        mock_push.assert_called_once_with("alt fallback", "U123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
