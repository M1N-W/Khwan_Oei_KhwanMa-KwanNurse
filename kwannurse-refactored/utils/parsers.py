# -*- coding: utf-8 -*-
"""
Parsers Utility Module
Functions for parsing and normalizing various input formats
"""
import re
import json
from datetime import datetime
from config import get_logger, TIME_OF_DAY_MAP

logger = get_logger(__name__)


def parse_date_iso(s):
    """
    Validate and parse date string to datetime.date
    Accepts: YYYY-MM-DD, YYYY-MM-DDT00:00:00Z
    Returns: datetime.date or None
    """
    if not s:
        return None
    
    try:
        # Handle dict input
        if isinstance(s, dict):
            for k in ("date", "value", "original"):
                if k in s and isinstance(s[k], str):
                    s = s[k]
                    break
            else:
                s = json.dumps(s, ensure_ascii=False)
        
        # Parse ISO format
        s2 = str(s).split("T")[0]
        return datetime.strptime(s2.strip(), "%Y-%m-%d").date()
    
    except Exception:
        # Try to extract date from string
        try:
            m = re.search(r'(\d{4}-\d{2}-\d{2})', str(s))
            if m:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except Exception:
            logger.exception("Error parsing date: %s", s)
    
    return None


def parse_time_hhmm(s):
    """
    Normalize various time formats to 'HH:MM'
    Accepts: HH:MM, HH.MM, ISO format, etc.
    Returns: 'HH:MM' string or None
    """
    if not s:
        return None
    
    try:
        # Handle dict input
        if isinstance(s, dict):
            s = json.dumps(s, ensure_ascii=False)
        
        s = str(s).strip()
        
        # Extract time from ISO format
        if "T" in s:
            parts = s.split("T")[-1]
            s = parts
        
        # Parse HH:MM format
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            h = int(parts[0]) % 24
            m = int(parts[1]) % 60
            return f"{h:02d}:{m:02d}"
        
        # Try to extract HH:MM or HH.MM
        m = re.search(r'(\d{1,2})[:.]\s*(\d{2})', s)
        if m:
            h = int(m.group(1)) % 24
            m2 = int(m.group(2)) % 60
            return f"{h:02d}:{m2:02d}"
    
    except Exception:
        logger.exception("Error parsing time: %s", s)
    
    return None


def resolve_time_from_params(sys_time_param, timeofday_param):
    """
    Resolve time from system time or time-of-day parameter
    Priority: explicit time > time-of-day mapping
    Returns: 'HH:MM' string or None
    """
    # Try explicit time first
    t = parse_time_hhmm(sys_time_param) if sys_time_param else None
    if t:
        return t
    
    # Try time-of-day mapping
    if not timeofday_param:
        return None
    
    # Handle dict input
    if isinstance(timeofday_param, dict):
        for k in ("value", "name", "original", "displayName"):
            if k in timeofday_param:
                timeofday_param = timeofday_param[k]
                break
        else:
            timeofday_param = json.dumps(timeofday_param, ensure_ascii=False)
    
    # Map to standard time
    if isinstance(timeofday_param, str):
        key = timeofday_param.strip().lower()
        
        # Direct mapping
        if key in TIME_OF_DAY_MAP:
            return TIME_OF_DAY_MAP[key]
        
        # Fuzzy matching
        if "morning" in key or "เช้า" in key:
            return TIME_OF_DAY_MAP["morning"]
        if "afternoon" in key or "บ่าย" in key or "pm" in key:
            return TIME_OF_DAY_MAP["afternoon"]
        if "evening" in key or "เย็น" in key:
            return TIME_OF_DAY_MAP["evening"]
        if "noon" in key or "เที่ยง" in key:
            return TIME_OF_DAY_MAP["noon"]
    
    return None


def normalize_phone_number(raw):
    """
    Normalize phone number to Thai mobile format (0xxxxxxxxx)
    Handles: +66, 66, 081-234-5678, etc.
    Returns: '0xxxxxxxxx' or None
    """
    if not raw:
        return None
    
    s = str(raw).strip()
    
    # Remove non-digits except +
    s = re.sub(r"[^\d+]", "", s)
    
    # Handle international format
    if s.startswith("+"):
        if s.startswith("+66"):
            s = "0" + s[3:]
        else:
            s = s.lstrip("+")
    elif s.startswith("66") and len(s) > 2:
        s = "0" + s[2:]
    
    return s


def is_valid_thai_mobile(s):
    """
    Validate Thai mobile number format
    Must be: 10 digits, starts with 0, second digit 6-9
    Returns: boolean
    """
    if not s:
        return False
    
    if not s.isdigit():
        return False
    
    return (
        len(s) == 10 and
        s.startswith("0") and
        s[1] in "6789"
    )
