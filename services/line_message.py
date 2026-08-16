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
from services.line_copy import LINE_COPY

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (LINE API limits)
# ---------------------------------------------------------------------------
MAX_TEXT_CHARS = 5_000
MAX_FLEX_ALT_TEXT_CHARS = 400
MAX_QUICK_REPLY_ITEMS = 13
MAX_MESSAGES_PER_CALL = 5
LINE_COLORS = {
    "brand": "#1565C0", "success": "#2E7D32", "attention": "#B26A00",
    "urgent": "#B3261E", "surface": "#FFFFFF", "text": "#1F2937",
    "text_secondary": "#5B6472",
}


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
    style: str = "primary", color: Optional[str] = None,
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
    button = {"type": "button", "action": action, "style": style, "height": "sm"}
    if style == "primary":
        button["color"] = color or LINE_COLORS["brand"]
    return button


def flex_separator() -> dict:
    """Build a Flex separator component."""
    return {"type": "separator"}


def actionable_flex_fallback(title: str, next_action: str) -> str:
    """Build a short, accessible Flex fallback within LINE's character limit."""
    return f"{title}: {next_action}"[:MAX_FLEX_ALT_TEXT_CHARS]


def flex_card_header(title: str, color: str = LINE_COLORS["brand"]) -> dict:
    """Build the standard clinical card header."""
    return {
        "type": "box", "layout": "vertical", "backgroundColor": color,
        "paddingAll": "16px",
        "contents": [flex_text(title, weight="bold", size="lg", color="#FFFFFF")],
    }


def flex_status_pill(label: str, color: str = LINE_COLORS["success"]) -> dict:
    """Build a compact semantic status label for a Flex body."""
    return {
        "type": "box", "layout": "horizontal", "contents": [{
            "type": "box", "layout": "vertical", "backgroundColor": color,
            "cornerRadius": "20px", "paddingAll": "4px", "paddingStart": "10px",
            "paddingEnd": "10px", "contents": [flex_text(label, weight="bold", size="sm", color="#FFFFFF")],
        }],
    }


def flex_body_text(text: str, *, secondary: bool = False) -> dict:
    """Build readable mobile body text; ``xs`` is intentionally never used."""
    return flex_text(text, size="sm" if secondary else "md", color=(
        LINE_COLORS["text_secondary"] if secondary else LINE_COLORS["text"]
    ))


def flex_footer_cta(label: str, *, action_text: str | None = None, action_uri: str | None = None) -> dict:
    """Build one brand-colour primary CTA for a patient card footer."""
    return flex_button(
        label, action_type="uri" if action_uri else "message", action_text=action_text,
        action_uri=action_uri, style="primary", color=LINE_COLORS["brand"],
    )


def flex_bubble(
    body_components: list[dict],
    header_text: Optional[str] = None,
    footer_components: Optional[list[dict]] = None,
    header_background_color: str = LINE_COLORS["brand"],
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
            "spacing": "md",
            "paddingAll": "16px",
        },
    }
    if header_text:
        bubble["header"] = {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": header_background_color,
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": header_text, "color": "#FFFFFF", "weight": "bold", "size": "lg"}
            ],
        }
    if footer_components:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": footer_components,
            "spacing": "sm",
            "paddingAll": "16px",
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


def build_wound_photography_guide() -> dict:
    """Flex bubble: photography tips before patient sends wound photo."""
    tips = [
        ("☀️", "ถ่ายใกล้หน้าต่างหรือที่แสงธรรมชาติสว่าง"),
        ("📏", "วางเหรียญหรือไม้บรรทัดข้างแผลเพื่อแสดงขนาด"),
        ("🎯", "ให้แผลอยู่กลางภาพ ชัดเจน ไม่เบลอ"),
        ("🚫", "ห้ามกรองสีหรือแต่งภาพก่อนส่ง"),
    ]
    tip_rows = [
        {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
            {"type": "text", "text": icon, "size": "xl", "flex": 0},
            {"type": "text", "text": tip, "wrap": True, "size": "sm", "color": "#555555"},
        ]}
        for icon, tip in tips
    ]
    return {
        "type": "flex",
        "altText": "วิธีถ่ายภาพแผล: ถ่ายให้แผลชัด ไม่เบลอ วางไม้บรรทัดหรือเหรียญข้างแผล แล้วกดส่งรูปแผลค่ะ",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical",
                "backgroundColor": "#1565C0",
                "contents": [
                    {"type": "text", "text": "📸 วิธีถ่ายภาพแผล",
                     "color": "#FFFFFF", "weight": "bold", "size": "md"},
                    {"type": "text", "text": "เพื่อให้พยาบาลเห็นแผลชัดเจนที่สุด",
                     "color": "#BBDEFB", "size": "xs"},
                ],
            },
            "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": tip_rows},
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{
                    "type": "button", "style": "primary", "color": "#1565C0",
                    "action": {"type": "message", "label": "📷 ส่งรูปแผล", "text": "ส่งรูปแผล"},
                }],
            },
        },
    }


_SEVERITY_CONFIG = {
    "high":   {"color": "#B3261E", "icon": "🔴", "label": "ความรุนแรงสูง",    "needs_nurse": True},
    "medium": {"color": "#B26A00", "icon": "🟠", "label": "ความรุนแรงปานกลาง","needs_nurse": True},
    "low":    {"color": "#2E7D32", "icon": "🟢", "label": "ความรุนแรงต่ำ",    "needs_nurse": False},
}


def build_wound_flex_result(severity: str, observations: list, advice: str, confidence: float) -> dict:
    """Flex bubble: wound analysis result, color-coded so non-tech users understand instantly."""
    severity = str(severity or "").strip().lower()
    cfg = _SEVERITY_CONFIG.get(severity, _SEVERITY_CONFIG["medium"])
    obs_rows = (
        [{"type": "text", "text": f"• {o}", "wrap": True, "size": "sm", "color": "#555555"}
         for o in observations]
        if observations else
        [{"type": "text", "text": "ไม่พบสิ่งผิดปกติชัดเจน", "size": "sm", "color": "#888888"}]
    )
    nurse_notice = ([{
        "type": "text",
        "text": "⚠️ พยาบาลจะได้รับการแจ้งเตือนเพื่อตรวจสอบ",
        "wrap": True, "size": "xs", "color": "#B71C1C", "weight": "bold",
    }] if cfg["needs_nurse"] else [])

    return {
        "type": "flex",
        "altText": f"ผลคัดกรองเบื้องต้น: {cfg['label']}\n{advice}"[:MAX_FLEX_ALT_TEXT_CHARS],
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical",
                "backgroundColor": cfg["color"],
                "contents": [
                    {"type": "text", "text": f"{cfg['icon']} ผลคัดกรองเบื้องต้น",
                     "color": "#FFFFFF", "weight": "bold", "size": "lg"},
                    {"type": "text", "text": cfg["label"],
                     "color": "#FFFFFF", "size": "sm"},
                ],
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "สิ่งที่พบ", "weight": "bold", "size": "md"},
                    *obs_rows,
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": "คำแนะนำ", "weight": "bold",
                     "size": "md", "margin": "md"},
                    {"type": "text", "text": advice, "wrap": True, "size": "sm", "color": "#333333"},
                    *nurse_notice,
                ],
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "button", "style": "secondary",
                               "action": {"type": "message", "label": "📞 ติดต่อพยาบาล",
                                          "text": "ต้องการพูดคุยกับพยาบาล"}}],
            },
        },
    }


_GUIDE_COLORS = {
    "wound_care": "#1565C0",
    "physical_therapy": "#2E7D32",
    "dvt_prevention": "#1565C0", "medication": "#B26A00", "warning_signs": "#B3261E",
}
_GUIDE_ICONS = {
    "wound_care": "🩹",
    "physical_therapy": "🏃",
    "dvt_prevention": "🩸",
    "medication": "💊",
    "warning_signs": "⚠️",
}


def _build_guide_bubble(rec: dict) -> dict:
    key = rec.get("key", "")
    color = _GUIDE_COLORS.get(key, LINE_COLORS["brand"])
    icon = _GUIDE_ICONS.get(key, "📖")
    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": color, "paddingAll": "16px",
            "contents": [{"type": "text", "text": f"{icon} {rec['title']}",
                          "color": "#FFFFFF", "weight": "bold", "size": "md", "wrap": True}],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "16px",
            "contents": [{"type": "text", "text": rec.get("reason", ""),
                          "wrap": True, "size": "md", "color": LINE_COLORS["text_secondary"]}],
        },
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "16px",
            "contents": [{"type": "button", "style": "primary", "color": color, "height": "sm",
                          "action": {"type": "message", "label": "อ่านคำแนะนำ", "text": rec["title"]}}],
        },
    }


def build_education_carousel(recommendations: list) -> dict:
    """
    Flex Carousel: up to 3 personalized education guide bubbles.
    Falls back to a plain-text dict if no recommendations.
    """
    if not recommendations:
        return {"type": "text",
                "text": "ไม่พบคำแนะนำที่เหมาะสม กรุณาติดต่อพยาบาลโดยตรง"}
    bubbles = [_build_guide_bubble(r) for r in recommendations]
    return {
        "type": "flex",
        "altText": "ความรู้ที่แนะนำ: เลือกหัวข้อเพื่ออ่านคำแนะนำได้เลยค่ะ",
        "contents": {"type": "carousel", "contents": bubbles},
    }


def build_nurse_assigned_message(nurse_name: str, contact_link: str) -> dict:
    """
    Flex bubble: notify patient that their consultation was accepted by a nurse.
    Includes altText that acts as a full fallback message with the link.
    """
    fallback_text = (
        f"🏥 พยาบาล {nurse_name} รับคำขอปรึกษาของคุณแล้วค่ะ\n\n"
        f"คุณสามารถกดแอดไลน์พยาบาลเพื่อเริ่มสนทนาได้ที่นี่เลยนะคะ: {contact_link}"
    )
    # Ensure fallback text does not exceed altText limits
    alt_text = fallback_text[:399]

    return {
        "type": "flex",
        "altText": alt_text,
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#2E7D32",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "💚 พยาบาลรับคำขอแล้ว",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "lg",
                    }
                ],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": f"สวัสดีค่ะ พยาบาล {nurse_name} ได้รับคำขอปรึกษาของคุณแล้วค่ะ",
                        "wrap": True,
                        "size": "md",
                        "color": "#333333",
                    },
                    {
                        "type": "text",
                        "text": "คุณสามารถกดปุ่มด้านล่างเพื่อแอดไลน์และเริ่มสนทนาได้เลยค่ะ",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666",
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": LINE_COLORS["brand"],
                        "action": {
                            "type": "uri",
                            "label": "💬 แชทกับพยาบาล",
                            "uri": contact_link,
                        },
                    }
                ],
            },
        },
    }


def build_nurse_contact_message(contact_link: str) -> dict:
    """Compact Flex card that opens the nurse chat without a URL preview."""
    return {
        "type": "flex",
        "altText": "ติดต่อพยาบาล: กดเปิดแชตเพื่อปรึกษาพยาบาลได้เลยค่ะ",
        "contents": {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box", "layout": "vertical", "backgroundColor": "#2E7D32",
                "paddingAll": "16px",
                "contents": [{
                    "type": "text", "text": "👩🏻‍⚕️ ติดต่อพยาบาล",
                    "color": "#FFFFFF", "weight": "bold", "size": "lg", "wrap": True,
                }],
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "text", "text": LINE_COPY["nurse_contact"], "wrap": True, "size": "md"},
                    {"type": "text", "text": "กดปุ่มด้านล่างเพื่อเปิดแชตได้เลยนะคะ", "wrap": True, "size": "sm", "color": "#666666"},
                ],
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{
                    "type": "button", "style": "primary", "color": LINE_COLORS["brand"],
                    "action": {"type": "uri", "label": "💬 เปิดแชตพยาบาล", "uri": contact_link},
                }],
            },
        },
    }


def build_daily_checkin_reminder() -> dict:
    """
    Flex message: reminder to prompt daily symptom reporting check-in.
    """
    return {
        "type": "flex",
        "altText": "🔔 ได้เวลารายงานอาการประจำวันแล้วค่ะ: กดรายงานอาการเพื่อให้ทีมดูแลติดตามต่อค่ะ",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#2E7D32",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "🔔 รายงานอาการประจำวัน",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "md",
                    }
                ],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": LINE_COPY["daily_checkin"],
                        "wrap": True,
                        "size": "sm",
                        "color": "#333333",
                    }
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": LINE_COLORS["brand"],
                        "action": {
                            "type": "message",
                            "label": "📝 รายงานอาการตอนนี้",
                            "text": "รายงานอาการ",
                        },
                    }
                ],
            },
        },
    }


def build_user_manual_flex() -> dict:
    """
    Flex message: User manual guide for the bot.
    """
    return {
        "type": "flex",
        "altText": actionable_flex_fallback("คู่มือการใช้งาน", "กดรายงานอาการเพื่อเริ่มใช้งานได้เลยค่ะ"),
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": LINE_COLORS["brand"],
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "📖 คู่มือการใช้งานระบบ",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "lg",
                    }
                ],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "16px",
                "contents": [
                    {
                        "type": "text",
                        "text": "ดูแลและติดตามอาการหลังผ่าตัดได้ในแชตนี้ค่ะ",
                        "wrap": True,
                        "size": "md",
                        "color": LINE_COLORS["text"],
                    },
                    {
                        "type": "text",
                        "text": "เริ่มจากรายงานอาการประจำวัน แล้วทีมพยาบาลจะติดตามต่อค่ะ",
                        "wrap": True,
                        "size": "sm",
                        "color": LINE_COLORS["text_secondary"],
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": LINE_COLORS["brand"],
                        "action": {
                            "type": "message",
                            "label": "รายงานอาการ",
                            "text": "รายงานอาการ",
                        },
                    }
                ],
            },
        },
    }

