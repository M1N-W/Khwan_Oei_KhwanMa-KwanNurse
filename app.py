from flask import Flask, request, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

app = Flask(__name__)

# --- ส่วนตั้งค่า Google Sheets ---
# เชื่อมต่อกับ Google Sheets
def save_to_sheet(pain, wound, fever, mobility, risk_result):
    try:
        # กำหนดขอบเขตการเข้าถึง
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # โหลดกุญแจจากไฟล์ JSON
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)

        # เปิดไฟล์ Google Sheet (ต้องตั้งชื่อไฟล์ให้ตรงเป๊ะๆ)
        sheet = client.open('KhwanBot_Data').sheet1

        # เตรียมข้อมูลที่จะบันทึก (วันที่, เวลา, อาการต่างๆ)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [timestamp, pain, wound, fever, mobility, risk_result]

        # บันทึกลงแถวใหม่
        sheet.append_row(row)
        print("บันทึกข้อมูลสำเร็จ!")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการบันทึก: {e}")

# --- ส่วนคำนวณความเสี่ยง (Logic เดิมที่ปรับปรุงแล้ว) ---
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
    
    # ส่งข้อมูลไปบันทึกใน Sheet ก่อนส่งข้อความกลับ
    save_to_sheet(pain, wound, fever, mobility, risk_level)
    
    return message

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    intent_name = req.get('queryResult').get('intent').get('displayName')
    
    if intent_name == 'ReportSymptoms':
        parameters = req.get('queryResult').get('parameters')
        pain_score = parameters.get('pain_score')
        wound_status = parameters.get('wound_status')
        fever_check = parameters.get('fever_check')
        mobility_status = parameters.get('mobility_status')
        
        reply_text = calculate_risk(pain_score, wound_status, fever_check, mobility_status)
        
        return jsonify({
            "fulfillmentText": reply_text
        })

    return jsonify({"fulfillmentText": "ขอโทษค่ะ ระบบขัดข้องชั่วคราว"})

if __name__ == '__main__':
    app.run(port=5000, debug=True)