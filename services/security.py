# -*- coding: utf-8 -*-
"""
Webhook signature / token verification (Phase 4 P4-1).

Two threats this module mitigates:

1. **LINE event spoofing** — without signature verification, anyone who
   discovers ``/line/webhook`` can POST forged events impersonating any
   user (e.g. send fake wound images, trigger nurse alerts on someone
   else's behalf). LINE signs every webhook request with HMAC-SHA256
   keyed by the bot's Channel Secret; we recompute and compare in
   constant time.

2. **Dialogflow fulfillment forgery** — ``/webhook`` returns text that
   the LINE bot will reply to the user. Without auth, an attacker can
   craft fake Dialogflow payloads to make the bot reply with arbitrary
   content, leak audit data via crafted intents, etc. We support a
   shared bearer token (``DIALOGFLOW_WEBHOOK_TOKEN``) that the agent
   sends in an ``Authorization`` header.

Design choices:

* **Feature-flagged** — set ``WEBHOOK_VERIFY_DISABLED=true`` to skip
  checks (dev only). Production deployments MUST leave this unset.
* **Fail-closed when secret is set** — if ``LINE_CHANNEL_SECRET`` is
  configured, missing/invalid signatures return 401. This is intentional:
  silently accepting unsigned requests defeats the purpose.
* **Fail-open when secret is missing** — if no secret is configured
  AND verification is not explicitly disabled, we log a loud warning
  but still serve requests. This preserves zero-downtime upgrades:
  deploys that haven't set the env yet keep working, but ops sees the
  warning in the logs.
* **Constant-time compare** — uses ``hmac.compare_digest`` to avoid
  timing oracles.
* **Metrics** — every accept/reject is counted via ``services.metrics``
  so the dashboard ``/metrics`` endpoint can show abuse attempts.

References:
- LINE: https://developers.line.biz/en/reference/messaging-api/#signature-validation
- Dialogflow: https://cloud.google.com/dialogflow/es/docs/fulfillment-webhook#authentication
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from functools import wraps
from typing import Callable, Optional

from flask import Response, request

from config import get_logger
from services.metrics import incr

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Pure helpers (testable without Flask context)
# -----------------------------------------------------------------------------
def compute_line_signature(raw_body: bytes, channel_secret: str) -> str:
    """
    Compute the LINE signature for ``raw_body`` using ``channel_secret``.

    Returns base64-encoded HMAC-SHA256 — the same format LINE sends in
    the ``X-Line-Signature`` header.
    """
    digest = hmac.new(
        key=channel_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_line_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    channel_secret: str,
) -> bool:
    """
    Constant-time verify that ``signature_header`` matches the HMAC of
    ``raw_body``. Returns True only on a clean match.

    Returns False when:
    - ``channel_secret`` is empty (caller should fail-close upstream)
    - ``signature_header`` is missing/empty
    - the computed digest doesn't match
    """
    if not channel_secret or not signature_header:
        return False
    expected = compute_line_signature(raw_body, channel_secret)
    # ``compare_digest`` is constant-time; both sides must be str or bytes.
    return hmac.compare_digest(expected, signature_header)


def verify_bearer_token(
    auth_header: Optional[str],
    expected_token: str,
) -> bool:
    """
    Verify ``Authorization: Bearer <token>`` against ``expected_token``.

    Returns False when:
    - ``expected_token`` is empty (caller should fail-close)
    - header is missing, malformed, or token doesn't match
    """
    if not expected_token or not auth_header:
        return False
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].strip(), expected_token)


# -----------------------------------------------------------------------------
# Flask decorators (operate on flask.request)
# -----------------------------------------------------------------------------
def _verify_disabled() -> bool:
    """
    Read ``WEBHOOK_VERIFY_DISABLED`` lazily so tests can flip the env
    between calls without re-importing.
    """
    import os
    return os.environ.get("WEBHOOK_VERIFY_DISABLED", "").lower() in (
        "1", "true", "yes",
    )


def require_line_signature(view: Callable) -> Callable:
    """
    Decorator: enforce ``X-Line-Signature`` matches HMAC of raw body.

    Rejection paths:
    - Secret configured + bad signature → 401
    - Secret configured + missing signature → 401
    - Secret missing + verify not disabled → log warning, allow (one-time
      per process via WARN_ONCE flag would be nicer; keeping simple)
    - Secret missing + verify disabled → allow silently (dev mode)
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        # Lazy import so module load doesn't snapshot env at startup
        from config import LINE_CHANNEL_SECRET

        if _verify_disabled():
            incr("security.line.verify_disabled")
            return view(*args, **kwargs)

        if not LINE_CHANNEL_SECRET:
            incr("security.line.no_secret_configured")
            logger.warning(
                "LINE webhook called but LINE_CHANNEL_SECRET is not set — "
                "request allowed but THIS IS INSECURE in production"
            )
            return view(*args, **kwargs)

        signature = request.headers.get("X-Line-Signature", "")
        # ``request.get_data(cache=True)`` lets the downstream view still
        # call ``request.get_json()`` without re-reading the stream.
        raw_body = request.get_data(cache=True)

        if not verify_line_signature(raw_body, signature, LINE_CHANNEL_SECRET):
            incr("security.line.signature_invalid")
            logger.warning(
                "Rejected LINE webhook: signature mismatch "
                "(body_len=%d signature_present=%s)",
                len(raw_body or b""), bool(signature),
            )
            return Response(
                response='{"error":"invalid signature"}',
                status=401,
                mimetype="application/json",
            )

        incr("security.line.signature_valid")
        return view(*args, **kwargs)

    return wrapper


def require_dialogflow_token(view: Callable) -> Callable:
    """
    Decorator: enforce ``Authorization: Bearer <DIALOGFLOW_WEBHOOK_TOKEN>``.

    Same fail-open-when-unconfigured behavior as ``require_line_signature``.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        from config import DIALOGFLOW_WEBHOOK_TOKEN

        if _verify_disabled():
            incr("security.dialogflow.verify_disabled")
            return view(*args, **kwargs)

        if not DIALOGFLOW_WEBHOOK_TOKEN:
            incr("security.dialogflow.no_token_configured")
            logger.warning(
                "Dialogflow webhook called but DIALOGFLOW_WEBHOOK_TOKEN is "
                "not set — request allowed but consider enabling auth"
            )
            return view(*args, **kwargs)

        auth = request.headers.get("Authorization", "")
        if not verify_bearer_token(auth, DIALOGFLOW_WEBHOOK_TOKEN):
            incr("security.dialogflow.token_invalid")
            logger.warning(
                "Rejected Dialogflow webhook: bad/missing bearer token "
                "(header_present=%s)", bool(auth),
            )
            return Response(
                response='{"error":"invalid token"}',
                status=401,
                mimetype="application/json",
            )

        incr("security.dialogflow.token_valid")
        return view(*args, **kwargs)

    return wrapper
