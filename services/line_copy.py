"""Approved, non-clinical Thai copy for patient-facing LINE UI."""
from __future__ import annotations


LINE_COPY = {
    "cancel_hint": "ระหว่างทำรายการ กด “✕ ยกเลิก” หรือพิมพ์ “ยกเลิก” ได้ทุกเมื่อค่ะ",
    "generic_error": "ขออภัยค่ะ ระบบขัดข้องชั่วคราว\nกรุณาลองใหม่อีกครั้ง หรือติดต่อพยาบาลค่ะ",
    "unknown_command": "ขออภัยค่ะ ยังไม่เข้าใจข้อความนี้\nลองพิมพ์ “เมนู” หรือเลือกสิ่งที่ต้องการได้เลยค่ะ",
    "survey_thanks": "ขอบคุณสำหรับความคิดเห็นค่ะ 🙏",
    "followup_empty": "ยังไม่มีข้อมูลการติดตามค่ะ\nระบบจะส่งเตือนตามกำหนดหลังจำหน่ายค่ะ",
    "recommendations_sent": "ส่งคำแนะนำที่เหมาะกับคุณไว้แล้วค่ะ\nเลือกหัวข้อที่ต้องการอ่านได้เลยค่ะ",
    "nurse_contact": "พยาบาลพร้อมให้คำปรึกษาค่ะ\nกดปุ่มด้านล่างเพื่อเปิดแชตได้เลย",
    "daily_checkin": "วันนี้เป็นอย่างไรบ้างคะ?\nกดรายงานอาการเพื่อให้ทีมดูแลติดตามต่อค่ะ",
}


def line_copy(key: str, **values: str) -> str:
    """Return approved copy and interpolate only caller-supplied display values."""
    return LINE_COPY[key].format(**values)
