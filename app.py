from flask import Flask, request, jsonify
import gspread
from datetime import datetime
import os
import json
import requests
import logging

app = Flask(__name__)

# -----------------------
# Configuration / Logging
# -----------------------
DEBUG = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
LOG_LEVEL = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# -----------------------
# Google Sheets client
# -----------------------
def get_sheet_client():
    """
    รองรับสองวิธี: 1) credentials.json file  2) environment var 'GSPREAD_CREDENTIALS' (JSON content)
    คืนค่า gspread client หรือ None เมื่อไม่สามารถเชื่อมได้
    """
    try:
        # ถ้ามี env var ให้ใช้ก่อน (เช่นใน Render/Heroku)
        creds_env = os.environ.get("GSPREAD_CREDENTIALS")
        if creds_env:
            logger.debug("Using GSPREAD_CREDENTIALS from environment")
            creds_json = json.loads(creds_env)
            return gspread.service_account_from_dict(creds_json)

        # fallback to file
        if os.path.exists("credentials.json"):
            logger.debug("Using credentials.json file for gspread")
            return gspread.service_account(filename="credentials.json")

        logger.warning("No Google credentials found (credentials.json or GSPREAD_CREDENTIALS).")
        return None
    except Exception as e:
        logger.exception("Connect Sheet Error:")
        return None

# -----------------------
# LINE push helper
# -----------------------
def send_line_push(message):
    """
    ส่งข้อความไปยัง LINE (push message). ป้องกันการค้างด้วย timeout
    """
    try:
        access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
        target_id = os.environ.get('NURSE_GROUP_ID')

        if not access_token or not target_id:
            logger.warning("LINE token or NURSE_GROUP_ID not configured.")
            return False

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        payload = {"to": target_id, "messages": [{"type": "text", "text": message}]}

        resp = requests.post(url, headers=headers, json=payload, timeout=8)
        if resp.status_code != 200:
            logger.error("LINE push failed: %s %s", resp.status_code, resp.text)
            return False
        logger.info("Push Notification Sent")
        return True
    except requests.Timeout:
        logger.error("LINE push timeout")
        return False
    except Exception:
        logger.exception("Push Error")
        return False

# -----------------------
# Symptom logic
# -----------------------
def save_symptom_data(pain, wound, fever, mobility, risk_result):
    try:
        client = get_sheet_client()
        if client:
            sheet = client.open('KhwanBot_Data').sheet1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([timestamp, pain, wound, fever, mobility, risk_result], value_input_option='USER_ENTERED')
            logger.info("Symptom Saved")
    except Exception:
        logger.exception("Save Symptom Error")

def calculate_symptom_risk(pain, wound, fever, mobility):
    risk_score = 0
    try:
        p_val = int(pain) if pain is not None and str(pain).strip() != "" else 0
    except Exception:
        p_val = 0

    if p_val >= 8:
        risk_score += 3
    elif p_val >= 6:
        risk_score += 1

    wound_text = str(wound or "")
    if any(x in wound_text for x in ["หนอง", "มีกลิ่น", "แฉะ"]):
        risk_score += 3
    elif any(x in wound_text for x in ["บวมแดง", "อักเสบ"]):
        risk_score += 2

    fever_text = str(fever or "")
    mobility_text = str(mobility or "")
    if any(x in fever_text for x in ["มี", "ตัวร้อน", "fever", "hot"]):
        risk_score += 2
    if any(x in mobility_text for x in ["ไม่ได้", "ติดเตียง", "ไม่เดิน"]):
        risk_score += 1

    if risk_score >= 3:
        risk_level = "สูง (อันตราย)"
        msg = f"เสี่ยง{risk_level} (คะแนน {risk_score})\nกรุณากดปุ่ม 'ติดต่อพยาบาล' ทันที"
        notify_msg = f"DAILY REPORT (อาการแย่)\nRisk: {risk_score}\nPain: {pain}\nWound: {wound}\nกรุณาตรวจสอบทันที!"
        send_line_push(notify_msg)
    elif risk_score >= 2:
        risk_level = "ปานกลาง"
        msg = f"เสี่ยง{risk_level} (คะแนน {risk_score})\nเฝ้าระวังอาการใกล้ชิด 24 ชม."
    elif risk_score == 1:
        risk_level = "ต่ำ (เฝ้าระวัง)"
        msg = f"เสี่ยง{risk_level}\nโดยรวมปกติดี แต่ต้องสังเกตอาการ"
    else:
        risk_level = "ต่ำ (ปกติ)"
        msg = f"เสี่ยง{risk_level}\nแผลหายดี"

    save_symptom_data(pain, wound, fever, mobility, risk_level)
    return msg

# -----------------------
# Patient profile logic (with normalize)
# -----------------------
def save_profile_data(user_id, age, weight, height, bmi, diseases, risk_level):
    try:
        client = get_sheet_client()
        if client:
            sheet = client.open('KhwanBot_Data').worksheet('RiskProfile')
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            diseases_str = ", ".join(diseases) if isinstance(diseases, list) else str(diseases)
            sheet.append_row([timestamp, user_id, age, weight, height, bmi, diseases_str, risk_level], value_input_option='USER_ENTERED')
            logger.info("Profile Saved")
    except Exception:
        logger.exception("Save Profile Error")

def normalize_diseases(disease_param):
    if not disease_param:
        return []
    def extract_items(param):
        items = []
        if isinstance(param, list):
            raw = param
        else:
            raw = [param]
        for it in raw:
            if it is None:
                continue
            if isinstance(it, dict):
                v = it.get('name') or it.get('value') or it.get('original') or it.get('displayName')
                if not v:
                    try:
                        v = json.dumps(it, ensure_ascii=False)
                    except:
                        v = str(it)
            else:
                v = str(it)
            v = v.strip()
            if v:
                items.append(v)
        return items

    raw_items = extract_items(disease_param)
    mapping = {
        "hypertension": "ความดัน", "high blood pressure": "ความดัน", "blood pressure": "ความดัน",
        "diabetes": "เบาหวาน", "type 1 diabetes": "เบาหวาน", "type 2 diabetes": "เบาหวาน", "t2d": "เบาหวาน",
        "cancer": "มะเร็ง", "tumor": "มะเร็ง", "kidney": "ไต", "renal": "ไต",
        "heart": "หัวใจ", "cardiac": "หัวใจ",
        "ความดัน": "ความดัน", "เบาหวาน": "เบาหวาน", "มะเร็ง": "มะเร็ง", "ไต": "ไต", "หัวใจ": "หัวใจ",
        "ht": "ความดัน", "dm": "เบาหวาน",
    }
    negatives = {"none", "no", "no disease", "ไม่มี", "ไม่มีโรค", "healthy", "null", "n/a", "ไม่"}
    normalized = []
    seen = set()

    for raw in raw_items:
        s = raw.lower().strip()
        if s in negatives or any(neg in s for neg in ["no disease", "ไม่มี"]):
            continue
        found = False
        for key in sorted(mapping.keys(), key=lambda x: -len(x)):
            if key in s:
                canon = mapping[key]
                if canon not in seen:
                    normalized.append(canon)
                    seen.add(canon)
                found = True
                break
        if not found:
            candidate = raw.strip()
            if candidate and candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
    return normalized

def calculate_personal_risk(user_id, age, weight, height, disease):
    # default safe values
    risk_score = 0
    bmi = 0.0

    logger.debug("calculate_personal_risk input: age=%s weight=%s height=%s disease=%s", age, weight, height, disease)

    # validate numbers and compute BMI
    try:
        age_val = int(age) if age is not None and str(age).strip() != "" else None
    except:
        age_val = None
    try:
        weight_val = float(weight) if weight is not None and str(weight).strip() != "" else None
    except:
        weight_val = None
    try:
        height_cm = float(height) if height is not None and str(height).strip() != "" else None
    except:
        height_cm = None

    if height_cm and weight_val:
        height_m = height_cm / 100.0
        if height_m > 0:
            bmi = weight_val / (height_m ** 2)
    else:
        bmi = 0.0

    # scoring
    if age_val is not None and age_val >= 60:
        risk_score += 1
    if bmi >= 30:
        risk_score += 1
    elif bmi > 0 and bmi < 18.5:
        risk_score += 1

    disease_normalized = normalize_diseases(disease)
    logger.debug("normalized diseases: %s", disease_normalized)

    risk_diseases = {"เบาหวาน", "หัวใจ", "ความดัน", "ไต", "มะเร็ง"}
    if any(d in risk_diseases for d in disease_normalized):
        risk_score += 2

    if risk_score >= 4:
        risk_level = "สูง (High Risk)"
        desc = "มีความเสี่ยงสูงต่อภาวะแทรกซ้อน"
        advice = "พยาบาลจะติดตามใกล้ชิดเป็นพิเศษ"
    elif risk_score >= 2:
        risk_level = "ปานกลาง (Moderate Risk)"
        desc = "มีความเสี่ยงปานกลาง"
        advice = "คุมโรคประจำตัวและรายงานอาการทุกวัน"
    else:
        risk_level = "ต่ำ (Low Risk)"
        desc = "ความเสี่ยงเกณฑ์ปกติ"
        advice = "ปฏิบัติตัวตามคำแนะนำทั่วไป"

    diseases_str = ", ".join(disease_normalized) if disease_normalized else "ไม่มีโรคประจำตัว"

    message = (
        f"ผลประเมินความเสี่ยง\n"
        f"---------------------------\n"
        f"ข้อมูล: อายุ {age_val if age_val is not None else '-'}, BMI {bmi:.1f}\n"
        f"โรค: {diseases_str}\n"
        f"ระดับ: {risk_level}\n"
        f"({desc})\n"
        f"{advice}"
    )

    # save profile (non-blocking style: errors logged)
    try:
        save_profile_data(user_id, age_val, weight_val, height_cm, bmi, disease_normalized, risk_level)
    except Exception:
        logger.exception("Error saving profile")

    # notify nurse if high
    if risk_score >= 4:
        notify_msg = f"ผู้ป่วยใหม่ (เสี่ยงสูง)\nUser: {user_id}\nอายุ {age_val}, โรค {diseases_str}\nโปรดวางแผนเยี่ยมบ้าน"
        send_line_push(notify_msg)

    return message

# -----------------------
# Webhook endpoint
# -----------------------
def extract_user_id(original_req):
    """พยายามหา user id จาก payload หลายรูปแบบ (LINE, Telegram, etc.)"""
    try:
        # หลาย platform เก็บใน payload.data.source.userId หรือ payload.data.source.userId
        payload = original_req.get('payload') or {}
        # try nested common paths
        for path in [
            ("payload","data","source","userId"),
            ("payload","data","source","user_id"),
            ("payload","data","source","userId"),
            ("payload","data","user","id"),
            ("payload","user","id"),
        ]:
            cur = original_req
            found = True
            for p in path:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    found = False
                    break
            if found and cur:
                return cur
        # fallback simple keys
        if isinstance(payload, dict):
            # line: payload["data"]["source"]["userId"]
            try:
                return payload.get("data", {}).get("source", {}).get("userId")
            except:
                pass
    except Exception:
        logger.debug("Could not extract user id from original detect intent request")
    return "Unknown"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        req = request.get_json(silent=True, force=True)
        if not req:
            logger.warning("Empty request body")
            return jsonify({"fulfillmentText": "Request body empty"}), 400

        intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
        params = req.get('queryResult', {}).get('parameters', {})
        original_req = req.get('originalDetectIntentRequest', {}) or {}
        user_id = extract_user_id(original_req) or "Unknown"

        logger.info("Intent incoming: %s user=%s", intent, user_id)
        logger.debug("Params: %s", params)

        if intent == 'ReportSymptoms':
            res = calculate_symptom_risk(
                params.get('pain_score'),
                params.get('wound_status'),
                params.get('fever_check'),
                params.get('mobility_status')
            )
            return jsonify({"fulfillmentText": res}), 200

        elif intent == 'AssessPersonalRisk':
            # basic validation: require at least age/weight/height or disease
            age = params.get('age')
            weight = params.get('weight')
            height = params.get('height')
            disease = params.get('disease')
            if not any([age, weight, height, disease]):
                return jsonify({"fulfillmentText": "กรุณาระบุ อายุ/น้ำหนัก/ส่วนสูง หรือ โรคประจำตัว"}), 200

            res = calculate_personal_risk(user_id, age, weight, height, disease)
            return jsonify({"fulfillmentText": res}), 200

        elif intent == 'GetGroupID':
            return jsonify({"fulfillmentText": f"ID: {os.environ.get('NURSE_GROUP_ID', 'Not Set')}"})

        else:
            return jsonify({"fulfillmentText": "ขอโทษค่ะ บอทไม่เข้าใจคำสั่งนี้"}), 200

    except Exception:
        logger.exception("Unhandled error in webhook")
        # ส่งข้อความ fallback ให้ Dialogflow (ต้องเป็น 200 เพื่อ Dialogflow จะรับ)
        return jsonify({"fulfillmentText": "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่ภายหลัง"}), 200

if __name__ == '__main__':
    app.run(port=int(os.environ.get("PORT", 5000)), debug=DEBUG)
