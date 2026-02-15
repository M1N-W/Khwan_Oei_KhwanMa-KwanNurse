# 🔧 Quick Fix Guide - Exact Changes

## File 1: services/reminder.py

### Change #1: Line 127
```python
# BEFORE (WRONG):
success = send_line_push(user_id, message)

# AFTER (CORRECT):
success = send_line_push(message, user_id)
```

### Change #2: Line 211
```python
# BEFORE (WRONG):
send_line_push(NURSE_GROUP_ID, alert_message)

# AFTER (CORRECT):
send_line_push(alert_message, NURSE_GROUP_ID)
```
**Note:** This one was actually CORRECT in original - no change needed!

### Change #3: Function `get_reminder_summary()` starting around line 280
```python
# BEFORE (WRONG):
summary = {
    'user_id': user_id,
    'total_scheduled': len(user_reminders),
    'pending_response': len(pending),
    'scheduled_reminders': user_reminders,
    'pending_reminders': pending
}

# AFTER (CORRECT):
# Get all scheduled reminders
all_scheduled = get_scheduled_reminders()
user_scheduled = [r for r in all_scheduled if r.get('User_ID') == user_id]

# Get pending (sent but not responded)
pending = get_pending_reminders(user_id, None)

# Count by status
total_reminders = len(user_scheduled)
responded = len([r for r in user_scheduled if r.get('Status') == 'responded'])
pending_count = len([r for r in user_scheduled if r.get('Status') == 'sent'])
no_response = len([r for r in user_scheduled if r.get('Status') == 'no_response'])

# Get latest reminder
latest = None
if user_scheduled:
    sorted_reminders = sorted(
        user_scheduled,
        key=lambda x: x.get('Created_At', ''),
        reverse=True
    )
    latest = sorted_reminders[0] if sorted_reminders else None

summary = {
    'user_id': user_id,
    'total_reminders': total_reminders,
    'responded': responded,
    'pending': pending_count,
    'no_response': no_response,
    'latest': latest,
    'all_scheduled': user_scheduled,
    'pending_reminders': pending
}
```

---

## File 2: routes/webhook.py

### Change #1: Add intent routing (around line 66)
```python
# Add this after GetKnowledge handler:
elif intent == 'GetFollowUpSummary':
    return handle_get_followup_summary(user_id)
```

### Change #2: Add handler function (before `handle_unknown_intent`)
```python
def handle_get_followup_summary(user_id):
    """
    Handle GetFollowUpSummary intent
    FIXED: Added implementation for follow-up reminder summary
    """
    try:
        logger.info(f"GetFollowUpSummary request from {user_id}")
        
        # Get reminder summary from database
        summary = get_reminder_summary(user_id)
        
        # Check if there was an error
        if 'error' in summary:
            return jsonify({
                "fulfillmentText": (
                    "ขอโทษค่ะ เกิดข้อผิดพลาดในการดึงข้อมูล\n"
                    "กรุณาลองใหม่อีกครั้งหรือติดต่อพยาบาลค่ะ"
                )
            }), 200
        
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
                timestamp = latest.get('Created_At', '')
                
                # Format reminder type
                type_map = {
                    'day3': 'วันที่ 3',
                    'day7': 'วันที่ 7 (สัปดาห์แรก)',
                    'day14': 'วันที่ 14 (สัปดาห์ที่ 2)',
                    'day30': 'วันที่ 30 (ครบ 1 เดือน)'
                }
                type_display = type_map.get(reminder_type, reminder_type)
                
                # Format status
                status_map = {
                    'scheduled': '📅 กำหนดการแล้ว',
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
```

### Change #3: Update unknown intent message (line ~360)
```python
# Add "ติดตามหลังจำหน่าย" to the list:
return jsonify({
    "fulfillmentText": (
        f"ขอโทษค่ะ บอทยังไม่รองรับคำสั่ง '{intent}' ในขณะนี้\n\n"
        f"คุณสามารถใช้ฟีเจอร์หลักได้:\n"
        f"• รายงานอาการ\n"
        f"• ประเมินความเสี่ยง\n"
        f"• นัดหมายพยาบาล\n"
        f"• ความรู้และคำแนะนำ\n"
        f"• ติดตามหลังจำหน่าย\n"  # <-- ADD THIS LINE
        f"• ปรึกษาพยาบาล"
    )
}), 200
```

---

## File 3: services/notification.py

### No line number changes - just enhance the function
Replace the entire `send_line_push()` function with the enhanced version that includes:
- Message validation
- Target ID validation
- Better error handling
- Timeout handling

(See full fixed file for complete implementation)

---

## Summary of Changes

| File | Lines Changed | Type | Severity |
|------|--------------|------|----------|
| services/reminder.py | Line 127 | Fix parameter order | 🔴 Critical |
| services/reminder.py | Lines 280-320 | Fix return values | 🔴 Critical |
| routes/webhook.py | Line ~66 | Add intent route | 🟠 High |
| routes/webhook.py | Lines ~220-300 | Add handler function | 🟠 High |
| routes/webhook.py | Line ~360 | Update help text | 🟡 Low |
| services/notification.py | Lines 20-90 | Enhance validation | 🟡 Medium |

**Total Lines Changed:** ~150 lines across 3 files

---

## Testing Commands

```bash
# 1. Test reminder sending
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "queryResult": {
      "intent": {"displayName": "GetFollowUpSummary"},
      "parameters": {}
    },
    "session": "test-session/test-user-123"
  }'

# Expected: JSON response with follow-up summary

# 2. Test in Python
python3 -c "
from services.reminder import send_reminder
result = send_reminder('test_user_id', 'day3')
print('Success!' if result else 'Failed!')
"

# Expected: Success! (and LINE message received)

# 3. Check logs
tail -f /var/log/kwannurse/app.log | grep -i "reminder\|followup"

# Expected: No errors, see "Successfully sent" messages
```

---

## Rollback Plan (If Needed)

```bash
# 1. Restore from backup
cp services/reminder.py.backup services/reminder.py
cp routes/webhook.py.backup routes/webhook.py
cp services/notification.py.backup services/notification.py

# 2. Restart app
sudo systemctl restart kwannurse-bot
# OR
gunicorn app:app --reload

# 3. Verify old version running
curl http://localhost:5000/
# Check version number
```

---

## Deployment Checklist

- [ ] Backup current files
- [ ] Copy fixed files to production
- [ ] Verify file permissions (644)
- [ ] Restart application
- [ ] Check health endpoint (/)
- [ ] Test GetFollowUpSummary intent
- [ ] Test reminder sending
- [ ] Monitor logs for 1 hour
- [ ] User acceptance testing
- [ ] Update documentation

---

**Quick Reference:** Replace these 3 files with the fixed versions, restart the app, done! ✅
