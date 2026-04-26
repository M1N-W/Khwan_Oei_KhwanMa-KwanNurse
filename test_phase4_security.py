# -*- coding: utf-8 -*-
"""
Phase 4 P4-1: webhook signature/token verification tests.

Coverage:
1. Pure helpers: HMAC compute, signature verify, bearer verify
2. /line/webhook: rejects bad signature when secret is set
3. /line/webhook: accepts valid signature
4. /line/webhook: fail-open warning when secret not set
5. /line/webhook: WEBHOOK_VERIFY_DISABLED bypass
6. /webhook: rejects bad bearer when token is set
7. /webhook: accepts valid bearer
8. /webhook: fail-open when token not set
9. Constant-time compare (smoke check via valid+invalid)
"""
from __future__ import annotations

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RUN_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.security import (
    compute_line_signature,
    verify_line_signature,
    verify_bearer_token,
)


# -----------------------------------------------------------------------------
# 1. Pure helpers
# -----------------------------------------------------------------------------
class HelperTests(unittest.TestCase):

    def test_compute_line_signature_known_vector(self):
        # LINE doc-style example: known body + secret → known signature
        body = b'{"events":[]}'
        secret = "test-secret"
        sig = compute_line_signature(body, secret)
        # Recompute manually: HMAC-SHA256 base64
        import hmac, hashlib, base64
        expected = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        self.assertEqual(sig, expected)

    def test_verify_line_signature_valid(self):
        body = b'{"events":[]}'
        secret = "abc123"
        sig = compute_line_signature(body, secret)
        self.assertTrue(verify_line_signature(body, sig, secret))

    def test_verify_line_signature_tampered_body(self):
        secret = "abc123"
        sig = compute_line_signature(b'{"events":[]}', secret)
        self.assertFalse(verify_line_signature(b'{"events":[1]}', sig, secret))

    def test_verify_line_signature_missing_header(self):
        self.assertFalse(verify_line_signature(b"x", None, "secret"))
        self.assertFalse(verify_line_signature(b"x", "", "secret"))

    def test_verify_line_signature_empty_secret(self):
        # Even with a valid-looking sig, empty secret must reject
        self.assertFalse(verify_line_signature(b"x", "anything", ""))

    def test_verify_bearer_valid(self):
        self.assertTrue(verify_bearer_token("Bearer abc123", "abc123"))
        self.assertTrue(verify_bearer_token("bearer abc123", "abc123"))  # case-insensitive scheme

    def test_verify_bearer_wrong_token(self):
        self.assertFalse(verify_bearer_token("Bearer wrong", "abc123"))

    def test_verify_bearer_malformed(self):
        self.assertFalse(verify_bearer_token("abc123", "abc123"))  # no scheme
        self.assertFalse(verify_bearer_token("Basic abc123", "abc123"))  # wrong scheme
        self.assertFalse(verify_bearer_token(None, "abc123"))
        self.assertFalse(verify_bearer_token("", "abc123"))

    def test_verify_bearer_empty_expected(self):
        self.assertFalse(verify_bearer_token("Bearer abc", ""))


# -----------------------------------------------------------------------------
# 2-5. /line/webhook integration
# -----------------------------------------------------------------------------
class LineWebhookSecurityTests(unittest.TestCase):

    def setUp(self):
        # Fresh app per test (env-sensitive imports)
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        os.environ.pop("LINE_CHANNEL_SECRET", None)
        os.environ.pop("DIALOGFLOW_WEBHOOK_TOKEN", None)
        # Clear metrics so assertions don't bleed across tests
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client()

    def test_rejects_bad_signature_when_secret_set(self):
        os.environ["LINE_CHANNEL_SECRET"] = "real-secret"
        client = self._build_client()

        body = json.dumps({"events": []}).encode()
        resp = client.post(
            "/line/webhook",
            data=body,
            headers={"X-Line-Signature": "WRONG-SIG", "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"invalid signature", resp.data)

    def test_accepts_valid_signature(self):
        os.environ["LINE_CHANNEL_SECRET"] = "real-secret"
        client = self._build_client()

        body = json.dumps({"events": []}).encode()
        sig = compute_line_signature(body, "real-secret")
        resp = client.post(
            "/line/webhook",
            data=body,
            headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok", "events_received": 0})

    def test_rejects_missing_signature_when_secret_set(self):
        os.environ["LINE_CHANNEL_SECRET"] = "real-secret"
        client = self._build_client()

        resp = client.post(
            "/line/webhook",
            data=b'{"events":[]}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_fail_open_when_secret_not_set(self):
        # No LINE_CHANNEL_SECRET → request allowed but warning logged + counter
        client = self._build_client()

        resp = client.post(
            "/line/webhook",
            data=b'{"events":[]}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)

        from services.metrics import snapshot
        self.assertGreaterEqual(
            snapshot().get("security.line.no_secret_configured", 0), 1,
        )

    def test_disabled_flag_bypasses_verification(self):
        os.environ["LINE_CHANNEL_SECRET"] = "real-secret"
        os.environ["WEBHOOK_VERIFY_DISABLED"] = "true"
        try:
            client = self._build_client()
            resp = client.post(
                "/line/webhook",
                data=b'{"events":[]}',
                headers={"X-Line-Signature": "OBVIOUSLY-BAD"},
            )
            self.assertEqual(resp.status_code, 200)
            from services.metrics import snapshot
            self.assertGreaterEqual(
                snapshot().get("security.line.verify_disabled", 0), 1,
            )
        finally:
            os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)

    def test_signature_valid_metric_increments(self):
        os.environ["LINE_CHANNEL_SECRET"] = "real-secret"
        client = self._build_client()

        body = b'{"events":[]}'
        sig = compute_line_signature(body, "real-secret")
        client.post(
            "/line/webhook", data=body,
            headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
        )
        from services.metrics import snapshot
        self.assertGreaterEqual(
            snapshot().get("security.line.signature_valid", 0), 1,
        )


# -----------------------------------------------------------------------------
# 6-8. /webhook (Dialogflow) integration
# -----------------------------------------------------------------------------
class DialogflowWebhookSecurityTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)
        os.environ.pop("LINE_CHANNEL_SECRET", None)
        os.environ.pop("DIALOGFLOW_WEBHOOK_TOKEN", None)
        from services.metrics import reset
        reset()

    def _build_client(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        import app as app_module
        importlib.reload(app_module)
        return app_module.application.test_client()

    def _df_payload(self):
        return {
            "queryResult": {
                "intent": {"displayName": "GetKnowledge"},
                "parameters": {},
                "queryText": "ความรู้",
            },
            "session": "projects/x/agent/sessions/U-test-1",
        }

    def test_rejects_missing_bearer_when_token_set(self):
        os.environ["DIALOGFLOW_WEBHOOK_TOKEN"] = "secret-token"
        client = self._build_client()

        resp = client.post("/webhook", json=self._df_payload())
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"invalid token", resp.data)

    def test_rejects_wrong_bearer(self):
        os.environ["DIALOGFLOW_WEBHOOK_TOKEN"] = "secret-token"
        client = self._build_client()

        resp = client.post(
            "/webhook", json=self._df_payload(),
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_accepts_valid_bearer(self):
        os.environ["DIALOGFLOW_WEBHOOK_TOKEN"] = "secret-token"
        client = self._build_client()

        resp = client.post(
            "/webhook", json=self._df_payload(),
            headers={"Authorization": "Bearer secret-token"},
        )
        # 200 from handle_get_knowledge (menu since topic was empty)
        self.assertEqual(resp.status_code, 200)

    def test_fail_open_when_token_not_set(self):
        client = self._build_client()
        resp = client.post("/webhook", json=self._df_payload())
        self.assertEqual(resp.status_code, 200)
        from services.metrics import snapshot
        self.assertGreaterEqual(
            snapshot().get("security.dialogflow.no_token_configured", 0), 1,
        )

    def test_disabled_flag_bypasses_dialogflow_verification(self):
        os.environ["DIALOGFLOW_WEBHOOK_TOKEN"] = "secret-token"
        os.environ["WEBHOOK_VERIFY_DISABLED"] = "true"
        try:
            client = self._build_client()
            resp = client.post(
                "/webhook", json=self._df_payload(),
                headers={"Authorization": "Bearer obviously-wrong"},
            )
            self.assertEqual(resp.status_code, 200)
        finally:
            os.environ.pop("WEBHOOK_VERIFY_DISABLED", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
