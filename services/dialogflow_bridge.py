"""Minimal Dialogflow ES detect-intent client for the direct LINE bridge."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from config import get_logger

logger = get_logger(__name__)


class DialogflowBridgeError(RuntimeError):
    """Raised when the direct LINE bridge cannot call Dialogflow ES."""


def _load_service_account_info() -> dict:
    raw = os.environ.get("GSPREAD_CREDENTIALS") or os.environ.get("GOOGLE_CREDS_B64")
    if raw:
        if not os.environ.get("GSPREAD_CREDENTIALS"):
            raw = base64.b64decode(raw).decode("utf-8")
        return json.loads(raw)

    credentials_path = Path("credentials.json")
    if credentials_path.exists():
        return json.loads(credentials_path.read_text(encoding="utf-8"))

    raise DialogflowBridgeError("Google service-account credentials are not configured")


def detect_intent(user_id: str, text: str) -> dict:
    """Call Dialogflow ES and return its DetectIntent response as a dictionary."""
    if not user_id or not text:
        raise DialogflowBridgeError("user_id and text are required")

    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials

        info = _load_service_account_info()
        project_id = os.environ.get("DIALOGFLOW_PROJECT_ID") or info.get("project_id")
        if not project_id:
            raise DialogflowBridgeError("DIALOGFLOW_PROJECT_ID is not configured")

        credentials = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        session = AuthorizedSession(credentials)
        session_name = f"projects/{project_id}/agent/sessions/{user_id}"
        payload = {
            "queryInput": {
                "text": {
                    "text": text,
                    "languageCode": os.environ.get("DIALOGFLOW_LANGUAGE_CODE", "th"),
                }
            },
            "queryParams": {
                "payload": {
                    "source": "line",
                    "userId": user_id,
                }
            },
        }
        response = session.post(
            f"https://dialogflow.googleapis.com/v2/{session_name}:detectIntent",
            json=payload,
            timeout=float(os.environ.get("DIALOGFLOW_BRIDGE_TIMEOUT_SECONDS", "8")),
        )
        if response.status_code // 100 != 2:
            raise DialogflowBridgeError(
                f"Dialogflow detect-intent failed with HTTP {response.status_code}"
            )
        return response.json()
    except DialogflowBridgeError:
        raise
    except Exception as exc:
        logger.exception("Direct LINE Dialogflow bridge failed")
        raise DialogflowBridgeError("Dialogflow bridge request failed") from exc
