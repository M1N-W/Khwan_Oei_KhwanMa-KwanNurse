from flask import Flask, request, jsonify
import gspread
from datetime import datetime
import os
import json 
import requests # <--- (1) ต้องเพิ่มบรรทัดนี้ ไม่งั้นส่งไลน์ไม่ได้ครับ

app = Flask(__name__)

# --- ฟังก์ชันส่งข้อความหาพยาบาล (Messaging API Version) ---
def send_line_push(message):
    try:
        # ดึงค่าจาก Render Environment
        access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
        target_id = os.environ.get('NURSE_GROUP_ID') # ส่งเข้ากลุ่มพยาบาล
        
        if not access_token or not target_id:
            print("ตั้งค่าไม่ครบ (ขาด Token หรือ Group ID)")
            return

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        
        payload = {
            "to": target_id,
            "messages": [
                {
                    "type": "text",
                    "text": message
                }
            ]
        }
        
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        print(f"แจ้งเตือนพยาบาล: {response.status_code} {response.text}")
        
    except Exception as e:
        print(f"แจ้งเตือนล้มเหลว: {e}")

# --- ส่วนตั้งค่า Google Sheets ---
def save_to_sheet(pain, wound, fever, mobility, risk_result):
    try:
        # ตรวจสอบว่ามีไฟล์ credentials.json หรือไม่
        if not os.path.exists('credentials.json'):
            print("ไม่พบไฟล์ credentials.json (ระบบกำลังใช้ Environment Variable)")

        client = gspread.service_account(filename='credentials.json')
        sheet = client.open('KhwanBot_Data').sheet1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [timestamp, pain, wound, fever, mobility, risk_result]
        sheet.append_row(row, value_input_option='USER_ENTERED')
        print("บันทึกข้อมูลสำเร็จ!")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการบันทึก: {e}")

# --- ส่วนคำนวณความเสี่ยง ---
def calculate_risk(pain, wound, fever, mobility):
    risk_score = 0
    risk_level = "ต่ำ"
    message = ""

    # 1. ประเมินความปวด
    try:
        pain_val = int(pain)
    except:
        pain_val = 0
    
    if pain_val >= 8:
        risk_score += 3
    elif pain_val >= 6:
        risk_score += 1

    # 2. ประเมินแผล
    if "หนอง" in wound or "มีกลิ่น" in wound or "แฉะ" in wound:
        risk_score += 3
    elif "บวมแดง" in wound or "อักเสบ" in wound:
        risk_score += 2

    # 3. ประเมินไข้
    if "มี" in fever or "ตัวร้อน" in fever:
        risk_score += 2

    # 4. ประเมินการเดิน
    if "ไม่ได้" in mobility or "ติดเตียง" in mobility:
        risk_score += 1

    # --- สรุปผล ---
    if risk_score >= 3:
        risk_level = "สูง (อันตราย)"
        message = (
            f"⚠️ ผลการประเมิน: ความเสี่ยง{risk_level}\n"
            f"อาการน่าเป็นห่วง (คะแนน {risk_score})\n"
            f"กรุณากดปุ่ม 'ติดต่อพยาบาล' ทันทีค่ะ"
        )
        
        # (2) เพิ่มส่วนนี้กลับเข้ามาครับ (นิยามข้อความก่อนส่ง)
        notify_msg = (
            f"🚨 EMERGENCY REPORT 🚨\n"
            f"ผู้ป่วยมีความเสี่ยงสูง (คะแนน {risk_score})\n"
            f"ระดับความปวด: {pain}\n"
            f"แผล: {wound}\n"
            f"ไข้: {fever}\n"
            f"กรุณาตรวจสอบทันที!"
        )
        send_line_push(notify_msg) # ส่งไลน์หาพยาบาล

    elif risk_score >= 2: 
        risk_level = "ปานกลาง"
        message = (
            f"⚠️ ผลการประเมิน: ความเสี่ยง{risk_level}\n"
            f"มีอาการที่ต้องดูแลใกล้ชิด (คะแนน {risk_score})\n"
            f"หากอาการไม่ดีขึ้นใน 24 ชม. ให้แจ้งพยาบาลนะคะ"
        )
    elif risk_score == 1:
        risk_level = "ต่ำ (เฝ้าระวัง)"
        message = (
            f"🟡 ผลการประเมิน: ความเสี่ยง{risk_level}\n"
            f"โดยรวมยังปกติดีค่ะ มีแค่อาการบางอย่างต้องสังเกต\n"
            f"พักผ่อนและทานยาตามแพทย์สั่งนะคะ"
        )
    else:
        risk_level = "ต่ำ (ปกติ)"
        message = (
            f"✅ ผลการประเมิน: ความเสี่ยง{risk_level}\n"
            f"แผลและร่างกายฟื้นตัวได้ดีเยี่ยมค่ะ\n"
            f"ดูแลตัวเองตามคำแนะนำต่อไปนะคะ"
        )
    
    save_to_sheet(pain, wound, fever, mobility, risk_level)
    return message

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    try:
        intent_name = req.get('queryResult', {}).get('intent', {}).get('displayName')
    except Exception:
        intent_name = None
    
    print(f"Intent received: {intent_name}")

    if intent_name == 'ReportSymptoms':
        parameters = req.get('queryResult', {}).get('parameters', {})
        pain_score = parameters.get('pain_score')
        wound_status = parameters.get('wound_status', "")
        fever_check = parameters.get('fever_check', "")
        mobility_status = parameters.get('mobility_status', "")
        
        reply_text = calculate_risk(pain_score, wound_status, fever_check, mobility_status)
        
        return jsonify({
            "fulfillmentText": reply_text
        })

    return jsonify({"fulfillmentText": "ขอโทษค่ะ ระบบขัดข้องชั่วคราว หรือไม่เข้าใจคำสั่ง"})

if __name__ == '__main__':
    app.run(port=5000, debug=True)
