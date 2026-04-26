# -*- coding: utf-8 -*-
"""
EducationLog persistence (Quick-win D3-A).

Tracks every time a patient views/receives a knowledge guide. Used by the
nurse dashboard patient timeline so nurses can see at a glance what topics
the patient has been reading.

Schema (Google Sheets ``EducationLog``):

    Timestamp | User_ID | Topic | Source | Personalized

- ``Topic``: canonical key from the recommender (e.g. ``wound_care``,
  ``medication``, ``physical_therapy``, ``dvt_prevention``,
  ``warning_signs``). For free-form / unmatched queries, store the raw
  query text truncated to 100 chars.
- ``Source``: ``GetKnowledge`` (user asked) | ``RecommendKnowledge``
  (system recommended based on profile).
- ``Personalized``: ``true`` / ``false`` — whether the response was
  tailored using stored patient profile (Sprint 2 S2-3).

Privacy:
- We never store the guide content. Only the topic key.
- ``User_ID`` is the LINE user ID — same convention as other sheets.

Sheet auto-creation:
- If ``EducationLog`` worksheet does not exist on first write, this module
  creates it with the canonical header row. This keeps deployment
  friction low (no manual sheet setup needed).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from config import LOCAL_TZ, SHEET_EDUCATION_LOG, get_logger
from database.sheets import get_spreadsheet, get_worksheet
from database.retry import retry_sheet_op

logger = get_logger(__name__)


_HEADER = ["Timestamp", "User_ID", "Topic", "Source", "Personalized"]


def _get_or_create_sheet():
    """
    Return the EducationLog worksheet, auto-creating it with header row
    on first use. Returns ``None`` on any error (caller treats as no-op).
    """
    sheet = get_worksheet(SHEET_EDUCATION_LOG)
    if sheet is not None:
        return sheet

    spreadsheet = get_spreadsheet()
    if spreadsheet is None:
        logger.warning("education_logs: no spreadsheet handle; skip auto-create")
        return None

    try:
        sheet = spreadsheet.add_worksheet(
            title=SHEET_EDUCATION_LOG, rows=1000, cols=len(_HEADER),
        )
        sheet.append_row(_HEADER, value_input_option="USER_ENTERED")
        logger.info("education_logs: auto-created sheet '%s'", SHEET_EDUCATION_LOG)
        return sheet
    except Exception:
        logger.exception("education_logs: failed to auto-create sheet")
        return None


def save_education_view(
    user_id: str,
    topic: str,
    source: str,
    personalized: bool = False,
) -> bool:
    """
    Append one education-view row.

    Args:
        user_id: LINE user ID.
        topic: canonical topic key, or raw query (truncated 100 chars).
        source: ``GetKnowledge`` or ``RecommendKnowledge``.
        personalized: True if stored patient profile was used.

    Returns:
        bool: True on success, False otherwise. Callers MUST swallow
        failures — losing an audit row should never break the user reply.
    """
    if not user_id:
        return False

    try:
        sheet = _get_or_create_sheet()
        if sheet is None:
            return False

        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            user_id,
            (topic or "")[:100],
            source or "",
            "true" if personalized else "false",
        ]
        retry_sheet_op(
            lambda: sheet.append_row(row, value_input_option="USER_ENTERED"),
            op_name="education_logs.append",
        )
        logger.info(
            "education_logs: appended row user=%s topic=%s source=%s personalized=%s",
            user_id, topic, source, personalized,
        )
        return True

    except Exception:
        logger.exception("education_logs: failed to append row user_id=%s", user_id)
        return False


def get_recent_education(
    user_id: Optional[str] = None,
    days: int = 30,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Read recent EducationLog rows newest-first, optionally filtered by user.

    Args:
        user_id: if provided, filter to a single user.
        days: look-back window (default 30).
        limit: max rows to return.

    Returns:
        list of dicts with keys ``timestamp`` (datetime), ``user_id``,
        ``topic``, ``source``, ``personalized`` (bool). Empty list on
        any error or empty sheet.
    """
    try:
        sheet = get_worksheet(SHEET_EDUCATION_LOG)
        if sheet is None:
            return []
        values = sheet.get_all_values()
        if not values or len(values) < 2:
            return []

        headers = values[0]

        def col(name: str, default: int) -> int:
            return headers.index(name) if name in headers else default

        idx_ts = col("Timestamp", 0)
        idx_uid = col("User_ID", 1)
        idx_topic = col("Topic", 2)
        idx_source = col("Source", 3)
        idx_pers = col("Personalized", 4)

        cutoff = datetime.now(tz=LOCAL_TZ).timestamp() - days * 86400
        out: list[dict[str, Any]] = []

        for row in reversed(values[1:]):
            if len(row) <= idx_uid:
                continue
            if user_id and row[idx_uid] != user_id:
                continue

            ts_raw = row[idx_ts] if len(row) > idx_ts else ""
            try:
                ts = datetime.strptime(ts_raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
            except (ValueError, TypeError):
                continue
            if ts.timestamp() < cutoff:
                continue

            personalized_raw = (row[idx_pers] if len(row) > idx_pers else "").strip().lower()

            out.append({
                "timestamp": ts,
                "user_id": row[idx_uid],
                "topic": row[idx_topic] if len(row) > idx_topic else "",
                "source": row[idx_source] if len(row) > idx_source else "",
                "personalized": personalized_raw == "true",
            })
            if len(out) >= limit:
                break

        return out

    except Exception:
        logger.exception("education_logs: failed to read user_id=%s", user_id)
        return []
