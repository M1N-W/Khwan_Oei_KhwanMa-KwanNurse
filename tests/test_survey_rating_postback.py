"""Regression coverage for survey rating postbacks."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
os.environ.setdefault("FLASK_SECRET_KEY", "test-survey-rating")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SurveyRatingPostbackTests(unittest.TestCase):
    def test_survey_rating_is_acknowledged_without_dialogflow(self):
        from app import create_app

        app = create_app()
        with patch("services.notification.reply_line_message") as reply:
            response = app.test_client().post("/line/webhook", json={"events": [{
                "type": "postback",
                "replyToken": "reply-rating",
                "source": {"userId": "U1"},
                "postback": {"data": "action=survey_rating&rating=5"},
            }]})

        self.assertEqual(response.status_code, 200)
        reply.assert_called_once_with("reply-rating", "ขอบคุณสำหรับความคิดเห็นค่ะ 🙏")

    def test_unrelated_postback_is_not_acknowledged_as_a_rating(self):
        from app import create_app

        app = create_app()
        with patch("services.notification.reply_line_message") as reply:
            response = app.test_client().post("/line/webhook", json={"events": [{
                "type": "postback",
                "replyToken": "reply-other",
                "source": {"userId": "U1"},
                "postback": {"data": "action=other&rating=5"},
            }]})

        self.assertEqual(response.status_code, 200)
        reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
