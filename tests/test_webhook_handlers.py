# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WebhookHandlersTest(unittest.TestCase):

    def setUp(self):
        self.app = Flask("test_app")
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_routing_registration(self):
        from routes.webhook import register_routes
        # Verify register_routes registers all endpoints on the app
        register_routes(self.app)
        
        routes = [r.rule for r in self.app.url_map.iter_rules()]
        self.assertIn("/", routes)
        self.assertIn("/healthz", routes)
        self.assertIn("/readyz", routes)
        self.assertIn("/metrics", routes)
        self.assertIn("/track/<token>", routes)
        self.assertIn("/webhook", routes)
        self.assertIn("/line/webhook", routes)

    @patch("routes.webhook.handlers.symptoms.handle_report_symptoms")
    def test_dispatch_report_symptoms(self, mock_handler):
        from routes.webhook import _dispatch_intent
        mock_handler.return_value = ("symptom response", 200)
        
        res = _dispatch_intent("ReportSymptoms", "U123", {"param": "val"}, "query")
        self.assertEqual(res, ("symptom response", 200))
        mock_handler.assert_called_once_with("U123", {"param": "val"})

    @patch("routes.webhook.handlers.fallback.handle_after_hours_choice")
    def test_dispatch_after_hours_choice(self, mock_handler):
        from routes.webhook import _dispatch_intent
        mock_handler.return_value = ("after hours response", 200)
        
        res = _dispatch_intent("AfterHoursChoice", "U123", {}, "query")
        self.assertEqual(res, ("after hours response", 200))
        mock_handler.assert_called_once_with("U123", "query")

    @patch("routes.webhook.handlers.registration.handle_patient_identity")
    def test_dispatch_patient_identity(self, mock_handler):
        from routes.webhook import _dispatch_intent
        mock_handler.return_value = ("identity response", 200)
        
        res = _dispatch_intent("PatientIdentity", "U123", {"param": "val"}, "query")
        self.assertEqual(res, ("identity response", 200))
        mock_handler.assert_called_once_with("U123", {"param": "val"}, "query")
