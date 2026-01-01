# 🔧 คู่มือแก้ไขปัญหา KwanNurse-Bot

## 🐛 ปัญหาที่พบและวิธีแก้ไข

### 1. ปัญหา Intent Name ไม่ตรงกัน ❌

**อาการ**: บอทตอบว่า "ขอโทษค่ะ บอทไม่เข้าใจคำสั่งนี้"

**สาเหตุ**: 
- Dialogflow ส่ง intent `AssessRisk` 
- แต่โค้ดเช็คแค่ `AssessPersonalRisk`

**วิธีแก้ไข**: ✅ แก้ไขแล้วในโค้ดใหม่
```python
# รองรับทั้ง 2 ชื่อ
elif intent == 'AssessPersonalRisk' or intent == 'AssessRisk':
```

---

### 2. ปัญหา LINE Notification ไม่ส่ง ⚠️

**อาการ**: Log แสดง `WARNING LINE token or NURSE_GROUP_ID not configured.`

**วิธีแก้ไข**: ตั้งค่า Environment Variables ใน Render

#### ขั้นตอนการตั้งค่าใน Render:

1. เข้าไปที่ Dashboard ของ Web Service ใน Render
2. ไปที่แท็บ **Environment**
3. เพิ่ม Environment Variables ดังนี้:

```bash
# LINE Messaging API
CHANNEL_ACCESS_TOKEN=<YOUR_LINE_CHANNEL_ACCESS_TOKEN>

# LINE Group/Chat ID สำหรับรับการแจ้งเตือน
NURSE_GROUP_ID=<YOUR_LINE_GROUP_ID>

# Google Sheets Credentials (JSON format)
GSPREAD_CREDENTIALS=<YOUR_GOOGLE_SERVICE_ACCOUNT_JSON>

# Optional: Link to Google Sheet
WORKSHEET_LINK=https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit

# Debug mode (optional)
DEBUG=false
```

4. กด **Save Changes**
5. Render จะ auto-redeploy service

---

### 3. วิธีหา LINE Group ID 🔍

มี 2 วิธี:

#### วิธีที่ 1: ใช้ LINE Messaging API
```bash
# ส่งข้อความไปที่กลุ่ม แล้วดูจาก webhook event
# Group ID จะอยู่ใน event.source.groupId
```

#### วิธีที่ 2: ใช้ Intent พิเศษในบอท
1. พิมพ์ข้อความในกลุ่มที่ต้องการรับการแจ้งเตือน
2. ดู logs ใน Render จะเห็น `user_id` 
3. นำ ID นั้นมาใส่ใน `NURSE_GROUP_ID`

---

### 4. ปัญหา 404 Errors จาก UptimeRobot 🔄

**อาการ**: Log เต็มไปด้วย `HEAD / HTTP/1.1 404`

**วิธีแก้ไข**: ✅ เพิ่ม health check endpoint แล้ว
```python
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return jsonify({"status": "ok"}), 200
```

---

### 5. ตรวจสอบว่า Dialogflow Intent ตั้งค่าถูกต้อง ✓

ใน Dialogflow Console ต้องมี Intents ดังนี้:

#### Intent: `AssessRisk` หรือ `AssessPersonalRisk`
- **Training Phrases**: 
  - "ประเมินความเสี่ยง"
  - "ฉันอายุ 65 น้ำหนัก 98 กก ส่วนสูง 165 ซม เป็นเบาหวาน"
  
- **Parameters**:
  - `age` (number) - required
  - `weight` (number) - required  
  - `height` (number) - required
  - `disease` หรือ `diseases` (any) - required

- **Fulfillment**: Enable Webhook

#### Intent: `ReportSymptoms`
- **Parameters**:
  - `pain_score` (number, 0-10)
  - `wound_status` (text)
  - `fever_check` (text)
  - `mobility_status` (text)

---

## 🚀 การ Deploy ใหม่

### 1. อัปโหลดโค้ดใหม่ไปที่ Render

```bash
# ถ้าใช้ Git
git add app.py
git commit -m "Fix intent handling and add health check"
git push origin main

# Render จะ auto-deploy
```

### 2. ตรวจสอบ Logs

```bash
# ใน Render Dashboard > Logs
# ดูว่ามี error หรือไม่
```

### 3. ทดสอบ

```bash
# 1. ทดสอบ Health Check
curl https://kwannurse-bot.onrender.com/

# 2. ทดสอบใน LINE
พิมพ์: "ประเมินความเสี่ยง อายุ 65 น้ำหนัก 98 ส่วนสูง 165 เป็นเบาหวาน"
```

---

## 📊 ตัวอย่าง Flow ที่ถูกต้อง

### Successful Risk Assessment:
```
User: ประเมินความเสี่ยง อายุ 65 น้ำหนัก 98 ส่วนสูง 165 เป็นเบาหวาน

LOG: Intent incoming: AssessRisk user=xxx params={"age": 65.0, "weight": 98.0, "height": 165.0, "diseases": ["diabetes"]}

Bot: 📊 ผลประเมินความเสี่ยงของคุณ
     ---------------------------
     👤 ข้อมูล: อายุ 65, BMI 36.0
     🏥 โรค: เบาหวาน
     ⚠️ ระดับ: สูง (High Risk)
     (มีความเสี่ยงสูงต่อภาวะแทรกซ้อน)
     💡 พยาบาลจะติดตามใกล้ชิดเป็นพิเศษ

LINE Notification to Nurse Group:
🆕 ผู้ป่วยใหม่ (เสี่ยงสูง)
User: xxx
อายุ 65, โรค เบาหวาน
โปรดวางแผนเยี่ยม
```

---

## 🔐 Checklist ก่อน Deploy

- [ ] ตั้งค่า `CHANNEL_ACCESS_TOKEN` ใน Render
- [ ] ตั้งค่า `NURSE_GROUP_ID` ใน Render  
- [ ] ตั้งค่า `GSPREAD_CREDENTIALS` ใน Render
- [ ] อัปเดตโค้ด app.py ใหม่
- [ ] ตรวจสอบ Dialogflow Intent names
- [ ] ตรวจสอบ Dialogflow Webhook URL: `https://kwannurse-bot.onrender.com/webhook`
- [ ] ทดสอบส่งข้อความใน LINE

---

## 🆘 ถ้ายังมีปัญหา

1. ตรวจสอบ Render Logs แบบ real-time
2. ตรวจสอบว่า Dialogflow Webhook ทำงานหรือไม่
3. ลอง Test ใน Dialogflow Console ก่อน
4. ตรวจสอบว่า LINE Bot ถูก invite เข้ากลุ่มแล้วหรือยัง

---

## 📝 การปรับปรุงในโค้ดใหม่

1. ✅ รองรับทั้ง `AssessRisk` และ `AssessPersonalRisk`
2. ✅ รองรับทั้ง `disease` และ `diseases` parameter
3. ✅ เพิ่ม health check endpoint (`/`)
4. ✅ ปรับปรุง error logging
5. ✅ แสดง intent name ใน fallback response
