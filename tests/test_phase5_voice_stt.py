# -*- coding: utf-8 -*-
"""
Phase 5 P5-2: voice message STT (LINE audio → Gemini → triage).

Coverage:
 1. transcribe_audio: returns None when LLM disabled / empty bytes
 2. transcribe_audio: success path returns text + increments metric
 3. transcribe_audio: timeout returns None + circuit failure recorded
 4. transcribe_audio: shares vision quota
 5. handle_voice_event: missing message_id replies gracefully
 6. handle_voice_event: download fail logs status + replies
 7. handle_voice_event: transcribe fail logs status + replies
 8. handle_voice_event: unintelligible marker handled
 9. handle_voice_event: success path triages + replies + audits
10. handle_voice_event: high-risk triggers nurse push
11. is_audio_event helper
12. /line/webhook routes audio events to voice handler
13. voice_logs.save_voice_message: privacy (no transcript content stored)
14. voice_logs auto-create sheet when missing
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import requests

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))


# -----------------------------------------------------------------------------
# 1-4. transcribe_audio low-level
# -----------------------------------------------------------------------------
class TranscribeAudioTests(unittest.TestCase):

    def setUp(self):
        from services import llm
        from services.metrics import reset
        llm._reset_state_for_tests()
        reset()

    def test_disabled_when_no_provider(self):
        from services import llm
        with patch.object(llm, "is_enabled", return_value=False):
            self.assertIsNone(llm.transcribe_audio(b"abc"))

    def test_empty_bytes_returns_none(self):
        from services import llm
        with patch.object(llm, "is_enabled", return_value=True):
            self.assertIsNone(llm.transcribe_audio(b""))
            self.assertIsNone(llm.transcribe_audio(None))

    def test_success_returns_text(self):
        from services import llm
        from services.metrics import snapshot

        with patch.object(llm, "is_enabled", return_value=True), \
             patch.object(llm, "LLM_PROVIDER", "gemini"), \
             patch.object(llm, "_call_gemini_audio", return_value="ฉันปวดท้องมากค่ะ"):
            result = llm.transcribe_audio(b"fake-audio-bytes", mime_type="audio/mp4")

        self.assertEqual(result, "ฉันปวดท้องมากค่ะ")
        self.assertGreaterEqual(snapshot().get("llm.audio_call_success", 0), 1)

    def test_timeout_returns_none_and_records_failure(self):
        from services import llm
        from services.metrics import snapshot

        with patch.object(llm, "is_enabled", return_value=True), \
             patch.object(llm, "LLM_PROVIDER", "gemini"), \
             patch.object(llm, "_call_gemini_audio",
                          side_effect=requests.exceptions.Timeout()):
            result = llm.transcribe_audio(b"audio")

        self.assertIsNone(result)
        self.assertGreaterEqual(snapshot().get("llm.audio_call_timeout", 0), 1)
        # Circuit failure counter advanced
        self.assertGreaterEqual(llm._get_state_snapshot()["consecutive_failures"], 1)

    def test_circuit_open_skips_call(self):
        from services import llm
        from services.metrics import snapshot
        import time

        with patch.object(llm, "is_enabled", return_value=True):
            # Force circuit open
            llm._state["circuit_open_until"] = time.time() + 60
            try:
                with patch.object(llm, "_call_gemini_audio") as mock_call:
                    result = llm.transcribe_audio(b"audio")
                    self.assertIsNone(result)
                    mock_call.assert_not_called()
                self.assertGreaterEqual(
                    snapshot().get("llm.audio_skip_circuit_open", 0), 1,
                )
            finally:
                llm._state["circuit_open_until"] = 0.0


# -----------------------------------------------------------------------------
# 5-10. handle_voice_event orchestration
# -----------------------------------------------------------------------------
class HandleVoiceEventTests(unittest.TestCase):

    def setUp(self):
        from services.metrics import reset
        reset()

    def _evt(self, message_id="MSG-1", duration_ms=4500, user_id="U-voice-test-1"):
        return {
            "type": "message",
            "replyToken": "RT-abc",
            "source": {"userId": user_id},
            "message": {"id": message_id, "type": "audio", "duration": duration_ms},
        }

    def test_missing_message_id(self):
        from services import voice
        with patch("services.notification.reply_line_message") as reply, \
             patch("services.notification.download_line_content") as dl, \
             patch("database.voice_logs.save_voice_message") as save:
            voice.handle_voice_event(self._evt(message_id=""))
        reply.assert_called_once()
        dl.assert_not_called()
        save.assert_not_called()

    def test_download_fail(self):
        from services import voice
        with patch("services.notification.download_line_content", return_value=None), \
             patch("services.notification.reply_line_message") as reply, \
             patch("database.voice_logs.save_voice_message") as save:
            voice.handle_voice_event(self._evt())

        reply.assert_called_once()
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["status"], "download_fail")
        self.assertEqual(save.call_args.kwargs["transcription_length"], 0)

    def test_transcribe_fail(self):
        from services import voice
        with patch("services.notification.download_line_content", return_value=b"audio"), \
             patch("services.llm.transcribe_audio", return_value=None), \
             patch("services.notification.reply_line_message") as reply, \
             patch("database.voice_logs.save_voice_message") as save:
            voice.handle_voice_event(self._evt())

        reply.assert_called_once()
        self.assertIn("ขัดข้อง", reply.call_args[0][1])
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["status"], "transcribe_fail")

    def test_unintelligible_marker(self):
        from services import voice
        with patch("services.notification.download_line_content", return_value=b"audio"), \
             patch("services.llm.transcribe_audio", return_value="[ไม่สามารถถอดความได้]"), \
             patch("services.notification.reply_line_message") as reply, \
             patch("database.voice_logs.save_voice_message") as save:
            voice.handle_voice_event(self._evt())

        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["status"], "unintelligible")
        self.assertIn("ไม่ชัดเจน", reply.call_args[0][1])

    def test_empty_transcript(self):
        from services import voice
        with patch("services.notification.download_line_content", return_value=b"audio"), \
             patch("services.llm.transcribe_audio", return_value="   "), \
             patch("services.notification.reply_line_message"), \
             patch("database.voice_logs.save_voice_message") as save:
            voice.handle_voice_event(self._evt())

        self.assertEqual(save.call_args.kwargs["status"], "empty")

    def test_success_path_triages_and_replies(self):
        from services import voice
        triage = {"risk_level": "medium", "risk_score": 5, "flags": ["ปวดท้อง"]}
        with patch("services.notification.download_line_content", return_value=b"audio"), \
             patch("services.llm.transcribe_audio", return_value="ปวดท้องมากค่ะ"), \
             patch("services.nlp.analyze_free_text", return_value=triage), \
             patch("services.nlp.format_triage_message", return_value="🟡 ความเสี่ยงปานกลาง"), \
             patch("services.notification.reply_line_message") as reply, \
             patch("services.notification.send_line_push") as push, \
             patch("database.voice_logs.save_voice_message") as save:
            voice.handle_voice_event(self._evt())

        reply.assert_called_once()
        body = reply.call_args[0][1]
        self.assertIn("ได้ยินว่า", body)
        self.assertIn("ปวดท้องมากค่ะ", body)
        self.assertIn("ปานกลาง", body)
        push.assert_not_called()  # medium ≠ high

        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["status"], "ok")
        self.assertEqual(save.call_args.kwargs["transcription_length"], len("ปวดท้องมากค่ะ"))
        self.assertEqual(save.call_args.kwargs["duration_sec"], 4)  # 4500ms → 4s

    def test_high_risk_triggers_nurse_push(self):
        from services import voice
        triage = {"risk_level": "high", "risk_score": 9, "flags": ["เลือดออก"]}
        with patch("services.notification.download_line_content", return_value=b"audio"), \
             patch("services.llm.transcribe_audio", return_value="เจ็บหน้าอกมาก"), \
             patch("services.nlp.analyze_free_text", return_value=triage), \
             patch("services.nlp.format_triage_message", return_value="🔴 ความเสี่ยงสูง"), \
             patch("services.notification.reply_line_message"), \
             patch("services.notification.send_line_push") as push, \
             patch("database.voice_logs.save_voice_message"):
            voice.handle_voice_event(self._evt())

        push.assert_called_once()
        msg = push.call_args.kwargs.get("message") or push.call_args[0][1]
        self.assertIn("HIGH risk", msg)

    def test_long_transcription_truncated_in_reply(self):
        from services import voice
        long_text = "ก" * 500
        triage = {"risk_level": "low", "risk_score": 1, "flags": []}
        with patch("services.notification.download_line_content", return_value=b"audio"), \
             patch("services.llm.transcribe_audio", return_value=long_text), \
             patch("services.nlp.analyze_free_text", return_value=triage), \
             patch("services.nlp.format_triage_message", return_value="ok"), \
             patch("services.notification.reply_line_message") as reply, \
             patch("database.voice_logs.save_voice_message"):
            voice.handle_voice_event(self._evt())

        body = reply.call_args[0][1]
        self.assertIn("...", body)
        self.assertLess(len(body), len(long_text) + 100)


# -----------------------------------------------------------------------------
# 11. Helper
# -----------------------------------------------------------------------------
class IsAudioEventTests(unittest.TestCase):

    def test_audio_detected(self):
        from services.voice import is_audio_event
        self.assertTrue(is_audio_event({
            "type": "message", "message": {"type": "audio", "id": "x"},
        }))

    def test_image_not_detected(self):
        from services.voice import is_audio_event
        self.assertFalse(is_audio_event({
            "type": "message", "message": {"type": "image"},
        }))

    def test_non_message_not_detected(self):
        from services.voice import is_audio_event
        self.assertFalse(is_audio_event({"type": "follow"}))
        self.assertFalse(is_audio_event({}))


# -----------------------------------------------------------------------------
# 12. /line/webhook routing
# -----------------------------------------------------------------------------
class LineWebhookAudioRoutingTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("LINE_CHANNEL_SECRET", None)
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client()

    def test_audio_event_invokes_voice_handler(self):
        client = self._build_client()
        body = {
            "events": [{
                "type": "message",
                "replyToken": "RT-1",
                "source": {"userId": "U-voice-1"},
                "message": {"type": "audio", "id": "MSG-aud-1", "duration": 3000},
            }]
        }
        with patch("services.voice.handle_voice_event") as voice_handler:
            resp = client.post("/line/webhook", json=body)

        self.assertEqual(resp.status_code, 200)
        voice_handler.assert_called_once()

    def test_image_event_does_not_invoke_voice_handler(self):
        client = self._build_client()
        body = {
            "events": [{
                "type": "message",
                "message": {"type": "image", "id": "MSG-img-1"},
                "source": {"userId": "U-1"},
                "replyToken": "RT-2",
            }]
        }
        with patch("services.voice.handle_voice_event") as voice_handler, \
             patch("routes.webhook.handle_line_image_event") as image_handler:
            resp = client.post("/line/webhook", json=body)

        self.assertEqual(resp.status_code, 200)
        voice_handler.assert_not_called()
        image_handler.assert_called_once()


# -----------------------------------------------------------------------------
# 13-14. VoiceMessageLog persistence
# -----------------------------------------------------------------------------
class VoiceLogPersistenceTests(unittest.TestCase):

    def test_row_does_not_contain_transcript_content(self):
        """Privacy: only metadata (length, status) — never the transcript itself."""
        from database import voice_logs
        fake_sheet = MagicMock()
        with patch.object(voice_logs, "_get_or_create_sheet", return_value=fake_sheet):
            ok = voice_logs.save_voice_message(
                user_id="U-1", duration_sec=8, mime_type="audio/mp4",
                transcription_length=42, status="ok",
            )
        self.assertTrue(ok)
        row = fake_sheet.append_row.call_args[0][0]
        # 6 fields per schema, no transcript text
        self.assertEqual(len(row), 6)
        self.assertEqual(row[1], "U-1")
        self.assertEqual(row[2], "8")
        self.assertEqual(row[3], "audio/mp4")
        self.assertEqual(row[4], "42")
        self.assertEqual(row[5], "ok")

    def test_empty_user_id_returns_false(self):
        from database import voice_logs
        self.assertFalse(voice_logs.save_voice_message(
            user_id="", duration_sec=0, mime_type="audio/mp4",
            transcription_length=0, status="ok",
        ))

    def test_swallows_exceptions(self):
        from database import voice_logs
        fake_sheet = MagicMock()
        fake_sheet.append_row.side_effect = RuntimeError("sheets down")
        with patch.object(voice_logs, "_get_or_create_sheet", return_value=fake_sheet):
            self.assertFalse(voice_logs.save_voice_message(
                user_id="U-1", duration_sec=5, mime_type="audio/mp4",
                transcription_length=10, status="ok",
            ))

    def test_auto_creates_sheet_when_missing(self):
        from database import voice_logs
        fake_spreadsheet = MagicMock()
        new_sheet = MagicMock()
        fake_spreadsheet.add_worksheet.return_value = new_sheet
        with patch.object(voice_logs, "get_worksheet", return_value=None), \
             patch.object(voice_logs, "get_spreadsheet", return_value=fake_spreadsheet):
            sheet = voice_logs._get_or_create_sheet()

        self.assertIs(sheet, new_sheet)
        fake_spreadsheet.add_worksheet.assert_called_once()
        # Header row appended on creation
        new_sheet.append_row.assert_called_once()
        header = new_sheet.append_row.call_args[0][0]
        self.assertEqual(header[0], "Timestamp")
        self.assertEqual(header[5], "Status")


if __name__ == "__main__":
    unittest.main(verbosity=2)
