# -*- coding: utf-8 -*-
"""
Updated webhook.py - Add GetFollowUpSummary Intent Handler
แก้ไขปัญหา Rich Menu ที่ trigger ผิด Intent
"""

# เพิ่ม code นี้ใน routes/webhook.py

# ========================================
# Part 1: เพิ่มใน Intent Routing
# ========================================
# หาบรรทัดที่มี:
#     elif intent == 'GetKnowledge':
#         return handle_get_knowledge(params)
#
# เพิ่มหลังจากบรรทัดนั้น:

# elif intent == 'GetFollowUpSummary':
#     return handle_get_followup_summary(user_id)


# ========================================
# Part 2: เพิ่ม Handler Function
# ========================================
# เพิ่มก่อน function handle_unknown_intent(intent):

def handle_get_followup_summary(user_id):
    """
    Handle GetFollowUpSummary intent
    แสดงสรุปการติดตามผู้ป่วยหลังจำหน่าย
    
    Args:
        user_id: User's LINE ID
        
    Returns:
        JSON response with follow-up summary
    """
    try:
        from services.reminder import get_reminder_summary
        
        logger.info(f"GetFollowUpSummary request from {user_id}")
        
        # Get reminder summary from database
        summary = get_reminder_summary(user_id)
        
        # Check if user has any reminders
        if summary['total_reminders'] == 0:
            message = (
                "📋 ยังไม่มีข้อมูลการติดตามค่ะ\n\n"
                "หลังจากที่คุณจำหน่ายจากโรงพยาบาล\n"
                "ระบบจะเริ่มติดตามอาการของคุณอัตโนมัติ\n\n"
                "💡 ระบบจะส่งการเตือนในวันที่:\n"
                "   • วันที่ 3 หลังจำหน่าย\n"
                "   • วันที่ 7 (สัปดาห์แรก)\n"
                "   • วันที่ 14 (สัปดาห์ที่ 2)\n"
                "   • วันที่ 30 (ครบ 1 เดือน)"
            )
        else:
            # Build summary message
            message = (
                f"📊 สรุปการติดตามของคุณ\n"
                f"{'=' * 30}\n\n"
                f"📌 รวมทั้งหมด: {summary['total_reminders']} ครั้ง\n"
                f"✅ ตอบกลับแล้ว: {summary['responded']} ครั้ง\n"
                f"⏳ รอตอบกลับ: {summary['pending']} ครั้ง\n"
            )
            
            if summary['no_response'] > 0:
                message += f"⚠️ ไม่ตอบกลับ: {summary['no_response']} ครั้ง\n"
            
            message += "\n"
            
            # Add latest reminder info
            if summary.get('latest'):
                latest = summary['latest']
                reminder_type = latest.get('Reminder_Type', 'unknown')
                status = latest.get('Status', 'unknown')
                timestamp = latest.get('Timestamp', '')
                
                # Format reminder type
                type_map = {
                    'day3': 'วันที่ 3',
                    'day7': 'วันที่ 7',
                    'day14': 'วันที่ 14',
                    'day30': 'วันที่ 30'
                }
                type_display = type_map.get(reminder_type, reminder_type)
                
                # Format status
                status_map = {
                    'sent': '⏳ รอตอบกลับ',
                    'responded': '✅ ตอบกลับแล้ว',
                    'no_response': '⚠️ ไม่ตอบกลับ'
                }
                status_display = status_map.get(status, status)
                
                message += (
                    f"🔔 การติดตามล่าสุด:\n"
                    f"   📅 {type_display}\n"
                    f"   สถานะ: {status_display}\n"
                )
                
                if timestamp:
                    message += f"   ⏰ {timestamp}\n"
            
            message += (
                f"\n"
                f"💡 พยาบาลจะติดตามอาการของคุณ\n"
                f"เป็นประจำตามกำหนดการนะคะ"
            )
        
        return jsonify({"fulfillmentText": message}), 200
        
    except Exception as e:
        logger.exception(f"Error in GetFollowUpSummary: {e}")
        return jsonify({
            "fulfillmentText": (
                "ขอโทษค่ะ เกิดข้อผิดพลาดในการดึงข้อมูล\n"
                "กรุณาลองใหม่อีกครั้งหรือติดต่อพยาบาลค่ะ"
            )
        }), 200


# ========================================
# Part 3: ตัวอย่างการใช้งาน
# ========================================
"""
ตัวอย่าง Response:

Input (จาก LINE): "ติดตามหลังจำหน่าย"
Dialogflow Intent: GetFollowUpSummary
Output:

📊 สรุปการติดตามของคุณ
==============================

📌 รวมทั้งหมด: 2 ครั้ง
✅ ตอบกลับแล้ว: 1 ครั้ง
⏳ รอตอบกลับ: 1 ครั้ง

🔔 การติดตามล่าสุด:
   📅 วันที่ 7
   สถานะ: ⏳ รอตอบกลับ
   ⏰ 2026-01-06 09:00:00

💡 พยาบาลจะติดตามอาการของคุณ
เป็นประจำตามกำหนดการนะคะ
"""
