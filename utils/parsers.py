# -*- coding: utf-8 -*-
"""
Parsers Utility Module
Functions for parsing and normalizing various input formats
"""
import re
import json
from datetime import datetime
from config import get_logger, TIME_OF_DAY_MAP

from typing import Optional

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
    Accepts: HH:MM, HH.MM, ISO format, ISO with timezone suffix (+07:00, Z), etc.
    Returns: 'HH:MM' string or None
    """
    if not s:
        return None
    
    try:
        # Handle dict input
        if isinstance(s, dict):
            s = json.dumps(s, ensure_ascii=False)
        
        s = str(s).strip()
        
        # Extract time from ISO datetime (e.g. "2026-01-01T09:30:00Z")
        if "T" in s:
            s = s.split("T")[-1]
        
        # Bug #9 fix: strip timezone suffix before parsing (e.g. "09:30+07:00" → "09:30")
        # Match and remove trailing +HH:MM, -HH:MM, or Z
        import re as _re
        s = _re.sub(r'[+-]\d{2}:\d{2}$|Z$', '', s).strip()
        
        # Parse HH:MM[:SS] format
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1][:2].isdigit():
            h = int(parts[0]) % 24
            m = int(parts[1][:2]) % 60
            return f"{h:02d}:{m:02d}"
        
        # Try to extract HH:MM or HH.MM via regex
        m = re.search(r'(\d{1,2})[:.](\d{2})', s)
        if m:
            h = int(m.group(1)) % 24
            m2 = int(m.group(2)) % 60
            return f"{h:02d}:{m2:02d}"
    
    except Exception:
        logger.exception("Error parsing time: %s", s)
    
    return None


def parse_thai_colloquial_time(s: str) -> Optional[str]:
    """
    Parse informal Thai times into standard HH:MM.
    e.g. "บ่ายสองโมง" -> "14:00", "บ่าย 2 ครึ่ง" -> "14:30", "สิบโมงเช้า" -> "10:00", "14.30 น." -> "14:30"
    """
    if not s:
        return None
    try:
        s = str(s).strip().replace(" ", "")
        
        # 1. Try to extract HH:MM or HH.MM via regex first
        m = re.search(r'(\d{1,2})[:.](\d{2})', s)
        if m:
            h = int(m.group(1)) % 24
            m2 = int(m.group(2)) % 60
            return f"{h:02d}:{m2:02d}"
            
        # 2. Match Noon / Midnight
        if s in ("เที่ยง", "เที่ยงตรง", "12.00น", "12.00น."):
            return "12:00"
        if s == "เที่ยงคืน":
            return "00:00"
            
        minutes = "30" if "ครึ่ง" in s else "00"
        
        # 3. Night hours (ทุ่ม)
        if "ทุ่ม" in s:
            mapping = {"หนึ่ง": 19, "1": 19, "สอง": 20, "2": 20, "สาม": 21, "3": 21, "สี่": 22, "4": 22, "ห้า": 23, "5": 23}
            for k, v in mapping.items():
                if k in s:
                    return f"{v:02d}:{minutes}"
            return f"19:{minutes}"
            
        # 4. Afternoon hours (บ่าย / โมงเย็น)
        if "บ่าย" in s or "โมงเย็น" in s:
            if "โมงเย็น" in s:
                mapping = {"สี่": 16, "4": 16, "ห้า": 17, "5": 17, "หก": 18, "6": 18}
                for k, v in mapping.items():
                    if k in s:
                        return f"{v:02d}:{minutes}"
            if "บ่าย" in s:
                if "โมง" in s and "สอง" not in s and "สาม" not in s and "สี่" not in s and "1" not in s and "2" not in s and "3" not in s and "4" not in s:
                    return f"13:{minutes}"
                mapping = {"หนึ่ง": 13, "1": 13, "สอง": 14, "2": 14, "สาม": 15, "3": 15, "สี่": 16, "4": 16}
                for k, v in mapping.items():
                    if k in s:
                        return f"{v:02d}:{minutes}"
                return f"13:{minutes}"
                
        # 5. Morning hours (โมงเช้า / โมง)
        if "โมง" in s or "เช้า" in s:
            mapping = {
                "เจ็ด": 7, "7": 7,
                "แปด": 8, "8": 8,
                "เก้า": 9, "9": 9,
                "สิบเอ็ด": 11, "11": 11,
                "สิบ": 10, "10": 10,
            }
            for k in ("สิบเอ็ด", "11", "เจ็ด", "7", "แปด", "8", "เก้า", "9", "สิบ", "10"):
                if k in s:
                    return f"{mapping[k]:02d}:{minutes}"
            if "โมงเช้า" in s:
                return f"07:{minutes}"
                
    except Exception:
        logger.exception("Error parsing colloquial Thai time: %s", s)
    return None


def resolve_time_from_params(sys_time_param, timeofday_param):
    """
    Resolve time from system time or time-of-day parameter
    Priority: explicit time > time-of-day mapping > colloquial time
    Returns: 'HH:MM' string or None
    """
    # Try explicit time first
    t = parse_time_hhmm(sys_time_param) if sys_time_param else None
    if t:
        return t
        
    for param in (sys_time_param, timeofday_param):
        if param and isinstance(param, str):
            t_col = parse_thai_colloquial_time(param)
            if t_col:
                return t_col
    
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
    
    # Restore leading zero if stripped by Google Sheets auto-formatting
    if len(s) == 9 and s[0] in "6789":
        s = "0" + s
        
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
