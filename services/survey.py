# -*- coding: utf-8 -*-
"""
Survey Service Module
Handle milestone scheduling, dispatcher loop, and message formatting for surveys
"""
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from config import BASE_URL, LOCAL_TZ, get_logger
from database.surveys import (
    save_survey_schedule,
    has_scheduled_surveys,
    get_due_surveys,
    claim_survey,
    mark_survey_sent,
    handle_survey_failure,
)
from services.line_message import (
    build_text_message,
    flex_text,
    flex_button,
    flex_bubble,
    build_flex_message,
    push_rich_message,
    quick_reply_item,
    build_quick_reply_message,
)

logger = get_logger(__name__)

SURVEY_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSc8NM7wvIrhzo8zW6NbfvKI741KcEANGzc8BcdZsfCErqkQAQ/viewform"
MILESTONES = [30]


def schedule_milestone_surveys(user_id: str, activation_date: Optional[datetime] = None) -> bool:
    """
    Schedule 7, 14, 21, and 30-day satisfaction survey milestones for a patient.
    Checks has_scheduled_surveys first to prevent duplicate milestone creation.
    """
    if not user_id:
        return False

    if has_scheduled_surveys(user_id):
        logger.info("Survey milestones already scheduled for user=%s, skipping creation", user_id)
        return False

    if activation_date is None:
        activation_date = datetime.now(tz=LOCAL_TZ)
    elif activation_date.tzinfo is None:
        activation_date = activation_date.replace(tzinfo=LOCAL_TZ)

    success = True
    for day in MILESTONES:
        # Calculate target run date
        target_dt = activation_date + timedelta(days=day)
        # Normalize to 9:00 AM of that day
        target_dt = target_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        
        # Generate token
        token = secrets.token_urlsafe(16)
        
        # Save to database
        ok = save_survey_schedule(
            user_id=user_id,
            milestone_day=day,
            survey_url=SURVEY_FORM_URL,
            tracking_token=token,
            scheduled_at=target_dt
        )
        if not ok:
            success = False

    return success


def build_survey_message(tracking_url: str, milestone_day: int) -> list[dict]:
    """
    Build survey message payload. Returns a list of LINE message objects.
    Contains both Flex/Rich version and text version fallback depending on ENABLE_RICH_MESSAGES.
    """
    thai_day = f"{milestone_day} วัน"
    text = (
        f"🏥 สวัสดีค่ะ กรุณาช่วยตอบแบบสอบถามความพึงพอใจ "
        f"การใช้งานระบบขวัญเอ๋ยขวัญมาของคุณในรอบ {thai_day} เพื่อให้ทีมพยาบาลนำไปพัฒนาการดูแลต่อไปค่ะ\n\n"
        f"สามารถกดทำแบบสอบถามได้ที่ลิงก์นี้เลยนะคะ: {tracking_url}"
    )

    # Rich Flex Bubble
    body = [
        flex_text(f"📋 แบบสอบถามความพึงพอใจ", weight="bold", size="lg", color="#0066CC"),
        flex_text(f"รอบการดูแลรักษาครอบ {thai_day}", size="md", color="#333333"),
        flex_text("ความคิดเห็นของคุณมีความสำคัญในการปรับปรุงบริการของทีมพยาบาลค่ะ", size="sm", color="#666666"),
    ]
    footer = [
        flex_button("ทำแบบสอบถาม", action_type="uri", action_uri=tracking_url, style="primary")
    ]
    bubble = flex_bubble(
        body_components=body,
        footer_components=footer,
        header_text="📋 ขวัญเอ๋ยขวัญมา Survey",
        header_background_color="#0066CC",
    )
    flex_msg = build_flex_message(f"แบบสอบถามความพึงพอใจรอบ {thai_day}", bubble)

    return [flex_msg, build_survey_rating_question()]


# Star-rating quick replies for satisfaction survey (Task 4B).
_SURVEY_STAR_QUICK_REPLIES = [
    quick_reply_item("⭐ 5 (ดีมาก)", "5"),
    quick_reply_item("⭐ 4 (ดี)", "4"),
    quick_reply_item("⭐ 3 (ปานกลาง)", "3"),
    quick_reply_item("⭐ 2 (พอใช้)", "2"),
    quick_reply_item("⭐ 1 (ควรปรับปรุง)", "1"),
]


def build_survey_rating_question() -> dict:
    """
    Build a star-rating quick reply text message for the satisfaction survey.
    Patients tap 1-5 stars directly in chat (Task 4B).

    Returns:
        dict: LINE text message object with quickReply section.
    """
    return build_quick_reply_message(
        "⭐ คุณพึงพอใจการใช้งานขวัญเอ๋ยขวัญมาอยู่ในระดับใดคะ? (กดดาวเพื่อแนะนำได้เลยค่ะ)",
        _SURVEY_STAR_QUICK_REPLIES,
    )


def process_due_surveys(now_dt: Optional[datetime] = None) -> int:
    """
    Background job execution loop for sending due surveys.
    Called by APScheduler every 1 minute.
    """
    try:
        if now_dt is None:
            now_dt = datetime.now(tz=LOCAL_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=LOCAL_TZ)

        due = get_due_surveys(now_dt=now_dt)
        if not due:
            return 0

        # Process chronologically (oldest row first)
        due.sort(key=lambda s: s["row_num"])
        processed_count = 0

        for survey in due:
            row_num = survey["row_num"]
            user_id = survey["user_id"]
            milestone_day = survey["milestone_day"]
            token = survey["tracking_token"]

            owner_id = f"survey_worker_{os.getpid()}_{uuid.uuid4().hex[:6]}"

            # Lock the row
            if not claim_survey(row_num, user_id, owner_id, now_dt=now_dt):
                continue

            try:
                # Construct tracking URL
                tracking_url = f"{BASE_URL}/track/{token}"
                messages = build_survey_message(tracking_url, milestone_day)

                # Send rich message (automatically falls back to plain text if ENABLE_RICH_MESSAGES=false)
                sent = push_rich_message(messages, user_id)
                if sent:
                    mark_survey_sent(row_num, user_id, now_dt=now_dt)
                    processed_count += 1
                else:
                    handle_survey_failure(row_num, user_id, "LINE message sending failed", now_dt=now_dt)
            except Exception as ex:
                logger.error("Failed to send survey row_num=%d user=%s: %s", row_num, user_id, ex)
                handle_survey_failure(row_num, user_id, f"Unexpected error: {str(ex)}", now_dt=now_dt)

        return processed_count
    except Exception:
        logger.exception("Error in process_due_surveys dispatcher loop")
        return 0
