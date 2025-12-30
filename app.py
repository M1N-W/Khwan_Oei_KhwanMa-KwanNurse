from flask import Flask, request, jsonify
import gspread
from datetime import datetime
import os
import json
import requests

app = Flask(__name__)

# ==========================================
# 🔧 CONFIGURATION & UTILS (ส่วนตั้งค่าระบบ)
# ==========================================

def get_sheet_client():
    """เชื่อมต่อ Google Sheet แบบปลอดภัย"""
    try:
        if not os.path.exists('credentials.json'):
            print("⚠️ Warning: ไม่พบไฟล์ credentials.json (อาจจะรันบน Cloud)")
        return gspread.service_account(filename='credentials.json')
    except Exception as e:
        print(f"❌ Connect Sheet Error: {e}")
        return None

def send_line_push(message):
    """ฟังก์ชันส่งข้อความหาพยาบาล (Reusable)"""
    try:
        access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
        target_id = os.environ.get('NURSE_GROUP_ID')
        
        if not access_token or not target_id:
            print("⚠️ Config Error: ขาด Token หรือ Group ID")
            return

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        payload = {
            "to": target_id,
            "messages": [{"type": "text", "text": message}]
        }
        requests.post(url, headers=headers, data=json.dumps(payload))
        print("✅ Push Notification Sent!")
    except Exception as e:
        print(f"❌ Push Error: {e}")

# ==========================================
# 🧠 LOGIC PART 1: DAILY SYMPTOM (อาการรายวัน)
# ==========================================

def save_symptom_data(pain, wound, fever, mobility, risk_result):
    try:
        client = get_sheet_client()
        if client:
            sheet = client.open('KhwanBot_Data').sheet1 # แผ่นที่ 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([timestamp, pain, wound, fever, mobility, risk_result], value_input_option='USER_ENTERED')
            print("✅ Symptom Saved")
    except Exception as e:
        print(f"❌ Save Symptom Error: {e}")

def calculate_symptom_risk(pain, wound, fever, mobility):
    # ... (Logic เดิมของคุณที่ผมย่อให้กระชับขึ้น) ...
    risk_score = 0
    
    # Pain Logic
    try: p_val = int(pain)
    except: p_val = 0
    if p_val >= 8: risk_score += 3
    elif p_val >= 6: risk_score += 1

    # Wound Logic
    if any(x in wound for x in ["หนอง", "มีกลิ่น", "แฉะ"]): risk_score += 3
    elif any(x in wound for x in ["บวมแดง", "อักเสบ"]): risk_score += 2

    # Fever & Mobility Logic
    if any(x in fever for x in ["มี", "ตัวร้อน"]): risk_score += 2
    if any(x in mobility for x in ["ไม่ได้", "ติดเตียง"]): risk_score += 1

    # Evaluation
    if risk_score >= 3:
        risk_level = "สูง (อันตราย)"
        msg = f"⚠️ เสี่ยง{risk_level} (คะแนน {risk_score})\nกรุณากดปุ่ม 'ติดต่อพยาบาล' ทันที"
        # Alert Nurse
        notify_msg = f"🚨 DAILY REPORT (อาการแย่)\nRisk: {risk_score}\nPain: {pain}\nWound: {wound}\nFever: {fever}\nCheck ASAP!"
        send_line_push(notify_msg)
    elif risk_score >= 2:
        risk_level = "ปานกลาง"
        msg = f"⚠️ เสี่ยง{risk_level} (คะแนน {risk_score})\nเฝ้าระวังอาการใกล้ชิด 24 ชม.นะคะ"
    elif risk_score == 1:
        risk_level = "ต่ำ (เฝ้าระวัง)"
        msg = f"🟡 เสี่ยง{risk_level}\nโดยรวมปกติดี แต่ต้องสังเกตอาการนะคะ"
    else:
        risk_level = "ต่ำ (ปกติ)"
        msg = f"✅ เสี่ยง{risk_level}\nแผลหายดี ยอดเยี่ยมมากค่ะ"

    save_symptom_data(pain, wound, fever, mobility, risk_level)
    return msg

# ==========================================
# 🧠 LOGIC PART 2: PATIENT PROFILE (ประเมินความเสี่ยงบุคคล)
# ==========================================

def save_profile_data(user_id, age, weight, height, bmi, diseases, risk_level):
    try:
        client = get_sheet_client()
        if client:
            # 🔥 บันทึกลง Tab ชื่อ 'RiskProfile' (ต้องสร้างรอไว้ก่อน)
            sheet = client.open('KhwanBot_Data').worksheet('RiskProfile')
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([timestamp, user_id, age, weight, height, bmi, diseases, risk_level], value_input_option='USER_ENTERED')
            print("✅ Profile Saved")
    except Exception as e:
        print(f"❌ Save Profile Error: {e}")

def assess_patient_risk(user_id, age, weight, height, diseases):
    """
    ฟังก์ชันประเมินความเสี่ยงพื้นฐานของผู้ป่วย (Risk Stratification)
    Logic: อายุเยอะ, อ้วน, มีโรคประจำตัว = เสี่ยงสูง
    """
    score = 0
    risk_factors = []
    
    # 1. คำนวณ BMI
    try:
        h_meter = float(height) / 100
        bmi = float(weight) / (h_meter ** 2)
        bmi = round(bmi, 2)
    except:
        bmi = 0

    # 2. Logic การให้คะแนน (Customizable)
    if float(age) > 60:
        score += 1
        risk_factors.append("ผู้สูงอายุ")
    
    if bmi > 30:
        score += 1
        risk_factors.append(f"ภาวะอ้วน (BMI {bmi})")
    
    # ตรวจสอบโรค (Keywords)
    diseases_str = str(diseases)
    if "เบาหวาน" in diseases_str or "Diabetes" in diseases_str:
        score += 2 # เบาหวานแผลหายช้า ให้คะแนนเยอะหน่อย
        risk_factors.append("เบาหวาน")
    if "ความดัน" in diseases_str or "หัวใจ" in diseases_str:
        score += 1
        risk_factors.append("โรคเรื้อรัง")

    # 3. สรุปผล
    if score >= 3:
        level = "สูง (High Risk)"
        advice = "🔴 คุณอยู่ในกลุ่มเสี่ยงสูง\nพยาบาลจะเข้ามาเยี่ยมบ่อยเป็นพิเศษนะคะ"
        # ถ้าความเสี่ยงพื้นฐานสูง แจ้งพยาบาลให้รับทราบเคสใหม่ทันที
        send_line_push(f"📋 NEW CASE REPORT\nคนไข้กลุ่มเสี่ยงสูง (Score {score})\nปัจจัย: {', '.join(risk_factors)}\nฝากดูแลด้วยนะคะ")
    elif score >= 1:
        level = "ปานกลาง (Moderate Risk)"
        advice = "🟡 คุณอยู่ในกลุ่มเสี่ยงปานกลาง\nควรดูแลแผลและคุมอาหารอย่างเคร่งครัดนะคะ"
    else:
        level = "ต่ำ (Low Risk)"
        advice = "✅ คุณอยู่ในกลุ่มเสี่ยงต่ำ\nร่างกายแข็งแรงดีมาก ปฏิบัติตัวตามปกติได้เลยค่ะ"

    # บันทึกข้อมูล
    save_profile_data(user_id, age, weight, height, bmi, diseases_str, level)

    return f"ผลประเมินสุขภาพเบื้องต้น:\nความเสี่ยงระดับ: {level}\n(BMI: {bmi})\n\n{advice}"

# ==========================================
# 🌐 WEBHOOK HANDLER (ตัวแยกทางเดินรถ)
# ==========================================

@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    try:
        intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
        params = req.get('queryResult', {}).get('parameters', {})
        
        # ดึง User ID (เผื่อใช้ระบุตัวตน)
        original_req = req.get('originalDetectIntentRequest', {})
        user_id = original_req.get('payload', {}).get('data', {}).get('source', {}).get('userId', 'Unknown')
    except Exception as e:
        print(f"❌ Parse Error: {e}")
        return jsonify({"fulfillmentText": "Error parsing request"})

    print(f"🔔 Intent Incoming: {intent}")

    # --- ROUTING ---
    
    if intent == 'GetGroupID':
        # (Logic หา ID แบบเดิม)
        try:
            source = original_req.get('payload', {}).get('data', {}).get('source', {})
            group_id = source.get('groupId') or source.get('roomId')
            if group_id: return jsonify({"fulfillmentText": f"🔑 Group ID: {group_id}"})
            else: return jsonify({"fulfillmentText": "บอทไม่ได้อยู่ในกลุ่มค่ะ"})
        except: return jsonify({"fulfillmentText": "Error"})

    elif intent == 'ReportSymptoms':
        # ฟีเจอร์ 1: รายงานอาการ
        res = calculate_symptom_risk(
            params.get('pain_score'), 
            params.get('wound_status'), 
            params.get('fever_check'), 
            params.get('mobility_status')
        )
        return jsonify({"fulfillmentText": res})

    elif intent == 'AssessRisk':
        # 🔥 ฟีเจอร์ 2: ประเมินความเสี่ยงบุคคล (ใหม่!)
        res = assess_patient_risk(
            user_id,
            params.get('age'),
            params.get('weight'),
            params.get('height'),
            params.get('diseases')
        )
        return jsonify({"fulfillmentText": res})

    return jsonify({"fulfillmentText": "ขอโทษค่ะ บอทไม่เข้าใจคำสั่งนี้"})

if __name__ == '__main__':
    app.run(port=5000, debug=True)