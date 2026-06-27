# -*- coding: utf-8 -*-
"""Canonical symptom risk-level helpers."""
from __future__ import annotations

from typing import Any

RISK_LEVEL_NORMAL = "normal"
RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"
RISK_LEVEL_CRITICAL = "critical"

RISK_RANK = {
    RISK_LEVEL_NORMAL: 0,
    RISK_LEVEL_LOW: 1,
    RISK_LEVEL_MEDIUM: 2,
    RISK_LEVEL_HIGH: 3,
    RISK_LEVEL_CRITICAL: 4,
}

_CANONICAL_LEVELS = set(RISK_RANK)


def risk_level_from_score(score: Any) -> str:
    """Return the canonical symptom risk code for a numeric risk score."""
    try:
        value = int(score)
    except (TypeError, ValueError):
        value = 0

    if value >= 5:
        return RISK_LEVEL_CRITICAL
    if value >= 3:
        return RISK_LEVEL_HIGH
    if value == 2:
        return RISK_LEVEL_MEDIUM
    if value == 1:
        return RISK_LEVEL_LOW
    return RISK_LEVEL_NORMAL


def normalize_risk_level(value: Any, score: Any = None) -> str:
    """
    Normalize canonical and legacy symptom risk labels to stable risk codes.

    Unknown or empty values fall back to score so existing Sheets rows remain
    readable without a migration.
    """
    raw = "" if value is None else str(value).strip()
    folded = raw.casefold()

    if folded in _CANONICAL_LEVELS:
        return folded

    if any(term in folded for term in ("อันตราย", "ต้องพบแพทย์ทันที", "critical")):
        return RISK_LEVEL_CRITICAL
    if any(term in folded for term in ("เสี่ยงสูง", "high")):
        return RISK_LEVEL_HIGH
    if any(term in folded for term in ("เสี่ยงปานกลาง", "ปานกลาง", "medium")):
        return RISK_LEVEL_MEDIUM
    if any(term in folded for term in ("เสี่ยงต่ำ", "ต่ำ", "low")):
        return RISK_LEVEL_LOW
    if any(term in folded for term in ("ปกติดี", "ปกติ", "normal")):
        return RISK_LEVEL_NORMAL

    return risk_level_from_score(score)


def risk_rank(level: Any, score: Any = None) -> int:
    """Return the sortable rank for a canonical or legacy symptom risk level."""
    return RISK_RANK[normalize_risk_level(level, score=score)]
