# -*- coding: utf-8 -*-
"""
LINE Message Delivery Layer (KWN-05)
=====================================
Pure builder functions for LINE message objects (Text, Quick Reply, Flex)
plus feature-flagged send helpers that wrap services/notification.py.

Design principles:
- Builders are pure functions returning plain dicts — no HTTP, no side effects.
- Send helpers (push_rich_message, reply_rich_message) check ENABLE_RICH_MESSAGES
  and fall back to plain text when the flag is off.
- Payload validation catches limit violations before the API call.

LINE limits (as of Messaging API v2):
- Text message: 5 000 chars per message object.
- Messages per API call: 5 objects max.
- Quick reply items: 13 max.
- Flex alt_text: 400 chars.
"""
from __future__ import annotations

from typing import Optional
from config import ENABLE_RICH_MESSAGES, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (LINE API limits)
# ---------------------------------------------------------------------------
MAX_TEXT_CHARS = 5_000
MAX_FLEX_ALT_TEXT_CHARS = 400
MAX_QUICK_REPLY_ITEMS = 13
MAX_MESSAGES_PER_CALL = 5


# ---------------------------------------------------------------------------
# Text message builder
# ---------------------------------------------------------------------------

def build_text_message(text: str) -> dict:
    """
    Build a LINE text message object.

    Args:
        text: Message body. Truncated to MAX_TEXT_CHARS if longer.

    Returns:
        dict: LINE message object ``{"type": "text", "text": ...}``
    """
    if not isinstance(text, str):
        text = str(text)
    if len(text) > MAX_TEXT_CHARS:
        logger.warning(
            "build_text_message: text truncated from %d to %d chars",
            len(text), MAX_TEXT_CHARS,
        )
        text = text[:MAX_TEXT_CHARS]
    return {"type": "text", "text": text}


# ---------------------------------------------------------------------------
# Quick Reply helpers
# ---------------------------------------------------------------------------

def quick_reply_item(label: str, text: str, image_url: Optional[str] = None) -> dict:
    """
    Build a Quick Reply button that sends a text message on tap.

    Args:
        label: Button label (≤20 chars recommended by LINE).
        text:  Text sent when tapped (≤300 chars).
        image_url: Optional icon URL (HTTPS, PNG/JPG, 24×24–72×72px).

    Returns:
        dict: Quick Reply item object.
    """
    action: dict = {"type": "message", "label": label[:20], "text": text[:300]}
    item: dict = {"type": "action", "action": action}
    if image_url:
        item["imageUrl"] = image_url
    return item


def quick_reply_postback(label: str, data: str, display_text: Optional[str] = None) -> dict:
    """
    Build a Quick Reply button that sends a postback event.

    Args:
        label:        Button label (≤20 chars).
        data:         Postback data payload (≤300 chars).
        display_text: Optional text displayed in chat on tap.

    Returns:
        dict: Quick Reply item object.
    """
    action: dict = {"type": "postback", "label": label[:20], "data": data[:300]}
    if display_text:
        action["displayText"] = display_text[:300]
    return {"type": "action", "action": action}


def build_quick_reply_message(text: str, items: list[dict]) -> dict:
    """
    Build a LINE text message with Quick Reply buttons.

    Args:
        text:  Message body (truncated to MAX_TEXT_CHARS).
        items: List of Quick Reply item dicts (capped at MAX_QUICK_REPLY_ITEMS).

    Returns:
        dict: LINE message object with ``quickReply`` section.
    """
    if len(items) > MAX_QUICK_REPLY_ITEMS:
        logger.warning(
            "build_quick_reply_message: capping items from %d to %d",
            len(items), MAX_QUICK_REPLY_ITEMS,
        )
        items = items[:MAX_QUICK_REPLY_ITEMS]

    msg = build_text_message(text)
    msg["quickReply"] = {"items": items}
    return msg


# ---------------------------------------------------------------------------
# Flex message helpers
# ---------------------------------------------------------------------------

def flex_text(
    text: str,
    weight: str = "regular",
    size: str = "md",
    color: Optional[str] = None,
    wrap: bool = True,
) -> dict:
    """Build a Flex text component."""
    component: dict = {"type": "text", "text": text, "weight": weight, "size": size, "wrap": wrap}
    if color:
        component["color"] = color
    return component


def flex_button(
    label: str,
    action_type: str = "message",
    action_text: Optional[str] = None,
    action_uri: Optional[str] = None,
    style: str = "primary",
) -> dict:
    """
    Build a Flex button component.

    Args:
        label:       Button label.
        action_type: ``"message"``, ``"uri"``, or ``"postback"``.
        action_text: Used when action_type is ``"message"`` or ``"postback"``.
        action_uri:  Used when action_type is ``"uri"``.
        style:       ``"primary"``, ``"secondary"``, or ``"link"``.
    """
    action: dict = {"type": action_type, "label": label}
    if action_type == "uri" and action_uri:
        action["uri"] = action_uri
    elif action_text:
        action["text"] = action_text
    return {"type": "button", "action": action, "style": style}


def flex_separator() -> dict:
    """Build a Flex separator component."""
    return {"type": "separator"}


def flex_bubble(
    body_components: list[dict],
    header_text: Optional[str] = None,
    footer_components: Optional[list[dict]] = None,
    header_background_color: str = "#1DB954",
) -> dict:
    """
    Build a Flex bubble container (single card).

    Args:
        body_components:           List of Flex components for the body box.
        header_text:               Optional header label text.
        footer_components:         Optional list of Flex components for footer box.
        header_background_color:   CSS hex color for header background.

    Returns:
        dict: Flex bubble container.
    """
    bubble: dict = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": body_components,
        },
    }
    if header_text:
        bubble["header"] = {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": header_background_color,
            "contents": [
                {"type": "text", "text": header_text, "color": "#FFFFFF", "weight": "bold", "size": "lg"}
            ],
        }
    if footer_components:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": footer_components,
        }
    return bubble


def build_flex_message(alt_text: str, contents: dict) -> dict:
    """
    Build a LINE Flex message object.

    Args:
        alt_text: Fallback text for notifications/older clients (≤400 chars).
        contents: A Flex container dict (bubble or carousel).

    Returns:
        dict: LINE message object ``{"type": "flex", ...}``
    """
    if len(alt_text) > MAX_FLEX_ALT_TEXT_CHARS:
        alt_text = alt_text[:MAX_FLEX_ALT_TEXT_CHARS]
    return {"type": "flex", "altText": alt_text, "contents": contents}


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

def validate_line_payload(messages: list[dict]) -> tuple[bool, str]:
    """
    Validate a list of LINE message objects before sending.

    Returns:
        (True, "") on success, (False, reason_str) on failure.
    """
    if not messages:
        return False, "messages list is empty"
    if len(messages) > MAX_MESSAGES_PER_CALL:
        return False, f"too many messages ({len(messages)} > {MAX_MESSAGES_PER_CALL})"

    for i, msg in enumerate(messages):
        msg_type = msg.get("type")
        if msg_type not in ("text", "flex", "sticker", "image", "video", "audio", "location", "template"):
            return False, f"message[{i}] has unknown type '{msg_type}'"
        if msg_type == "text":
            text = msg.get("text", "")
            if len(text) > MAX_TEXT_CHARS:
                return False, f"message[{i}] text too long ({len(text)} > {MAX_TEXT_CHARS})"
            quick_reply = msg.get("quickReply", {})
            items = quick_reply.get("items", [])
            if len(items) > MAX_QUICK_REPLY_ITEMS:
                return False, f"message[{i}] quickReply has too many items ({len(items)} > {MAX_QUICK_REPLY_ITEMS})"
        if msg_type == "flex":
            if not msg.get("altText"):
                return False, f"message[{i}] flex missing altText"
            if not msg.get("contents"):
                return False, f"message[{i}] flex missing contents"
    return True, ""


# ---------------------------------------------------------------------------
# Send helpers (feature-flagged wrappers around notification.py)
# ---------------------------------------------------------------------------

def push_rich_message(messages: list[dict], target_id: str) -> bool:
    """
    Send a list of LINE message objects via push API.

    When ENABLE_RICH_MESSAGES is False, falls back to sending only the first
    text message (plain text) to preserve existing behaviour.

    Args:
        messages:  List of LINE message objects (built with this module).
        target_id: Target LINE user/group ID.

    Returns:
        bool: True on success.
    """
    if not messages or not target_id:
        return False

    if not ENABLE_RICH_MESSAGES:
        # Fallback: send first text-compatible message as plain text
        text = _extract_fallback_text(messages[0])
        from services.notification import send_line_push
        return send_line_push(text, target_id)

    valid, reason = validate_line_payload(messages)
    if not valid:
        logger.error("push_rich_message: invalid payload — %s", reason)
        return False

    from services.notification import send_line_push_objects
    return send_line_push_objects(messages, target_id)


def reply_rich_message(reply_token: str, messages: list[dict]) -> bool:
    """
    Send a list of LINE message objects via reply API.

    When ENABLE_RICH_MESSAGES is False, falls back to sending the first
    message as plain text reply.

    Args:
        reply_token: ``event.replyToken`` from the LINE webhook.
        messages:    List of LINE message objects.

    Returns:
        bool: True on success.
    """
    if not reply_token or not messages:
        return False

    if not ENABLE_RICH_MESSAGES:
        text = _extract_fallback_text(messages[0])
        from services.notification import reply_line_message
        return reply_line_message(reply_token, text)

    valid, reason = validate_line_payload(messages)
    if not valid:
        logger.error("reply_rich_message: invalid payload — %s", reason)
        return False

    from services.notification import reply_line_message_objects
    return reply_line_message_objects(reply_token, messages)


def _extract_fallback_text(message: dict) -> str:
    """Extract plain text from any message object for fallback mode."""
    msg_type = message.get("type", "")
    if msg_type == "text":
        return message.get("text", "")
    if msg_type == "flex":
        return message.get("altText", "")
    return str(message)
