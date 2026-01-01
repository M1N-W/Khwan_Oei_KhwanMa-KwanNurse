# -*- coding: utf-8 -*-
"""
Khw anBot webhook (improved)
- normalize phone, resolve time-of-day, validate date not past
- better logging, safer sheet writes, helpful responses for Dialogflow
"""
from flask import Flask, request, jsonify
import gspread
from datetime import datetime, date
import os
import json
import requests
import logging
import re
from zoneinfo import ZoneInfo

# ---------- App config ----------
app = Flask(__name__)
DEBUG = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
logging.basicConfig(level=logging. DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Bangkok")
WORKSHEET_LINK = os.environ.get("WORKSHEET_LINK", "https://docs.google.com/spreadsheets/d/1Jteh4XLzgQM3YKMzUeW3PGuBjUkvnS61rm2IXfGPnPo/edit?usp=sharing")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
NURSE_GROUP_ID = os.environ.get("NURSE_GROUP_ID")

# ---------- gspread helper ----------
def get_sheet_client():
    try:
        creds_env = os.environ. get("GSPREAD_CREDENTIALS")
        if creds_env:
            creds_json = json.loads(creds_env)
            if hasattr(gspread, "service_account_from_dict"):
                return gspread. service_account_from_dict(creds_json)
        if os.path.exists("credentials.json"):
            return gspread.service_account(filename="credentials.json")
        logger.warning("No Google credentials found (credentials.json or GSPREAD_CREDENTIALS).")
    except Exception:
        logger.exception("Connect Sheet Error")
    return None

# ---------- LINE push helper ----------
def send_line_push(message):
    try:
        access_token = LINE_CHANNEL_ACCESS_TOKEN
        target_id = NURSE_GROUP_ID
        if not access_token or not target_id:
            logger.warning("LINE token or NURSE_GROUP_ID not configured.")
            return False
        url = 'https://api.line.me/v2/bot/message/push'
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {access_token}'}
        payload = {"to": target_id, "messages": [{"type": "text", "text": message}]}
        resp = requests.post(url, headers=headers, json=payload, timeout=8)
        if resp.status_code // 100 == 2:
            logger.info("Push Notification Sent to nurse group")
            return True
        else:
            logger.error("LINE push failed: %s %s", resp.status_code, resp.text)
            return False
    except Exception: 
        logger.exception("Push Error")
        return False

# ---------- Helpers for date/time/phone ----------
def parse_date_iso(s:  str):
    """Validate YYYY-MM-DD -> datetime. date or None.  Accept '2026-02-22T00:00:00Z' too."""
    if not s:
        return None
    try:
        if isinstance(s, dict):
            for k in ("date", "value", "original"):
                if k in s and isinstance(s[k], str):
                    s = s[k]
                    break
            else:
                s = json.dumps(s, ensure_ascii=False)
        s2 = str(s).split("T")[0]
        return datetime.strptime(s2. strip(), "%Y-%m-%d").date()
    except Exception:
        try:
            m = re.search(r'(\d{4}-\d{2}-\d{2})', str(s))
            if m:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except Exception:
            return None
    return None

def parse_time_hhmm(s: str):
    """Normalize various time shapes into 'HH:MM' or None."""
    if not s:
        return None
    try: 
        if isinstance(s, dict):
            s = json.dumps(s, ensure_ascii=False)
        s = str(s).strip()
        if "T" in s:
            parts = s.split("T")[-1]
            s = parts
        parts = s.split(":")
        if len(parts) >= 2 and parts[0]. isdigit() and parts[1].isdigit():
            h = int(parts[0]) % 24
            m = int(parts[1]) % 60
            return f"{h:02d}:{m:02d}"
        m = re.search(r'(\d{1,2}[:. ]\d{2})', s)
        if m:
            txt = m.group(1).replace('. ', ': ')
            ph = txt.split(':')
            h = int(ph[0]) % 24
            m2 = int(ph[1]) % 60
            return f"{h:02d}:{m2:02d}"
    except Exception:
        logger.exception("parse_time_hhmm error")
    return None

TIME_OF_DAY_MAP = {
    "morning": "09:00",
    "late_morning": "10:30",
    "noon": "12:00",
    "afternoon": "14:00",
    "evening":  "18:00",
    "night": "20:00",
    "เช้า": "09:00",
    "สาย": "10:30",
    "เที่ยง": "12:00",
    "บ่าย": "14:00",
    "เย็น": "18:00",
    "กลางคืน": "20:00"
}

def resolve_time_from_params(sys_time_param, timeofday_param):
    """Prefer explicit time; else map timeofday to default."""
    t = parse_time_hhmm(sys_time_param) if sys_time_param else None
    if t:
        return t
    if not timeofday_param:
        return None
    if isinstance(timeofday_param, dict):
        for k in ("value", "name", "original", "displayName"):
            if k in timeofday_param: 
                timeofday_param = timeofday_param[k]
                break
        else: 
            timeofday_param = json.dumps(timeofday_param, ensure_ascii=False)
    if isinstance(timeofday_param, str):
        key = timeofday_param.strip().lower()
        if key in TIME_OF_DAY_MAP: 
            return TIME_OF_DAY_MAP[key]
        if "morning" in key:
            return TIME_OF_DAY_MAP["morning"]
        if "afternoon" in key or "pm" in key:
            return TIME_OF_DAY_MAP["afternoon"]
        if "evening" in key:
            return TIME_OF_DAY_MAP["evening"]
    return None

def normalize_phone_number(raw: str):
    """Normalize various phone formats to local '0xxxxxxxxx' or return raw digits if unknown."""
    if not raw:
        return None
    s = str(raw).strip()
    s = re.sub(r"[^\d+]", "", s)
    if s. startswith("+"):
        if s.startswith("+66"):
            s = "0" + s[3:]
        else:
            s = s. lstrip("+")
    elif s.startswith("66") and len(s) > 2:
        s = "0" + s[2:]
    return s

def is_valid_thai_mobile(s: str):
    """Basic check:  10 digits starting with 0 and second digit 6-9 (typical mobile)"""
    if not s:
        return False
    if not s.isdigit():
        return False
    return len(s) == 10 and s. startswith("0") and s[1] in "6789"

# ---------- Sheet saves ----------
def save_appointment_to_sheet(user_id, name, phone, preferred_date, preferred_time, reason, status="New", assigned_to="", notes=""):
    try:
        client = get_sheet_client()
        if not client:
            logger.error("No gspread client available.")
            return False
        # แก้ไข: ใช้แท็บ Appointments จากไฟล์ KhwanBot_Data
        spreadsheet = client.open("KhwanBot_Data")
        sheet = spreadsheet.worksheet("Appointments")
        timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row = [timestamp, user_id, name or "", phone or "", preferred_date or "", preferred_time or "", reason or "", status, assigned_to, notes]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Saved appointment row for user %s", user_id)
        return True
    except Exception: 
        logger.exception("Error saving appointment to sheet")
        return False

def build_appointment_notification(user_id, preferred_date, preferred_time, reason):
    sheet_link = WORKSHEET_LINK
    return f"นัดใหม่ — user:  {user_id}\nวันที่:  {preferred_date} เวลา: {preferred_time}\nเรื่อง: {reason}\nดู sheet: {sheet_link}"

# ---------- Symptom & Personal Risk logic ----------
def save_symptom_data(pain, wound, fever, mobility, risk_result):
    try:
        client = get_sheet_client()
        if client:
            sheet = client.open('KhwanBot_Data').sheet1
            timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([timestamp, pain, wound, fever, mobility, risk_result], value_input_option='USER_ENTERED')
            logger.info("Symptom Saved")
    except Exception:
        logger.exception("Save Symptom Error")

def calculate_symptom_risk(pain, wound, fever, mobility):
    risk_score = 0
    try:
        p_val = int(pain) if pain is not None and str(pain).strip() != "" else 0
    except: 
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
        msg = f"⚠️ เสี่ยง{risk_level} (คะแนน {risk_score})\nกรุณากดปุ่ม 'ติดต่อพยาบาล' ทันที"
        notify_msg = f"🚨 DAILY REPORT (อาการแย่)\nRisk:  {risk_score}\nPain: {pain}\nWound: {wound}\nกรุณาตรวจสอบทันที!"
        send_line_push(notify_msg)
    elif risk_score >= 2:
        risk_level = "ปานกลาง"
        msg = f"⚠️ เสี่ยง{risk_level} (คะแนน {risk_score})\nเฝ้าระวังอาการใกล้ชิด 24 ชม.  เป็นการดีค่ะ"
    elif risk_score == 1:
        risk_level = "ต่ำ (เฝ้าระวัง)"
        msg = f"🟡 เสี่ยง{risk_level}\nโดยรวมปกติดี แต่ต้องสังเกตอาการนะคะ"
    else: 
        risk_level = "ต่ำ (ปกติ)"
        msg = f"✅ เสี่ยง{risk_level}\nแผลหายดี ยอดเยี่ยมมากค่ะ"
    save_symptom_data(pain, wound, fever, mobility, risk_level)
    return msg

def normalize_diseases(disease_param):
    """Extract and normalize disease/condition names from various formats."""
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
        "ความดัน": "ความดัน", "เบาหวาน": "เบาหวาน", "มะเร็ง": "มะเร็ง", "ไต": "ไต", "หัวใจ":  "หัวใจ",
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
            candidate = raw. strip()
            if candidate and candidate not in seen:
                normalized. append(candidate)
                seen. add(candidate)
    return normalized

def save_profile_data(user_id, age, weight, height, bmi, diseases, risk_level):
    try:
        client = get_sheet_client()
        if client:
            sheet = client.open('KhwanBot_Data').worksheet('RiskProfile')
            timestamp = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
            diseases_str = ", ".join(diseases) if isinstance(diseases, list) else str(diseases)
            sheet.append_row([timestamp, user_id, age, weight, height, bmi, diseases_str, risk_level], value_input_option='USER_ENTERED')
            logger.info("Profile Saved")
    except Exception: 
        logger.exception("Save Profile Error")

def calculate_personal_risk(user_id, age, weight, height, disease):
    """Calculate personal health risk based on age, BMI, and diseases."""
    risk_score = 0
    bmi = 0.0
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
        f"📊 ผลประเมินความเสี่ยงของคุณ\n"
        f"---------------------------\n"
        f"👤 ข้อมูล: อายุ {age_val if age_val is not None else '-'}, BMI {bmi:.1f}\n"
        f"🏥 โรค: {diseases_str}\n"
        f"⚠️ ระดับ:  {risk_level}\n"
        f"({desc})\n"
        f"💡 {advice}"
    )
    try:
        save_profile_data(user_id, age_val, weight_val, height_cm, bmi, disease_normalized, risk_level)
    except Exception:
        logger.exception("Error saving profile")
    if risk_score >= 4:
        notify_msg = f"🆕 ผู้ป่วยใหม่ (เสี่ยงสูง)\nUser: {user_id}\nอายุ {age_val}, โรค {diseases_str}\nโปรดวางแผนเยี่ยม"
        send_line_push(notify_msg)
    return message

# ---------- Dialogflow webhook ----------
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """Health check endpoint for UptimeRobot and monitoring services"""
    return jsonify({
        "status": "ok",
        "service": "KwanNurse-Bot",
        "version": "2.0",
        "timestamp": datetime.now(tz=LOCAL_TZ).isoformat()
    }), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    if not req:
        return jsonify({"fulfillmentText": "Request body empty"}), 400
    try:
        intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
        params = req.get('queryResult', {}).get('parameters', {}) or {}
        original_req = req. get('originalDetectIntentRequest', {}) or {}
        user_id = req.get('session', 'unknown').split('/')[-1]
    except Exception:
        logger.exception("Parse Error")
        return jsonify({"fulfillmentText": "Error parsing request"}), 200

    logger.info("Intent incoming: %s user=%s params=%s", intent, user_id, json.dumps(params, ensure_ascii=False))

    # --- Appointment Intent ---
    if intent == 'RequestAppointment':
        preferred_date_raw = params.get('date') or params.get('preferred_date') or params.get('date-original')
        preferred_time_raw = params.get('time') or params.get('preferred_time')
        timeofday_raw = params.get('timeofday') or params.get('time_of_day')
        reason = params.get('reason') or params.get('symptom') or params.get('description')
        name = params.get('name') or None
        phone_raw = params.get('phone-number') or params.get('phone') or None

        preferred_date = parse_date_iso(preferred_date_raw)
        preferred_time = resolve_time_from_params(preferred_time_raw, timeofday_raw)

        missing = []
        if not preferred_date:
            missing.append("วันที่ (รูปแบบ YYYY-MM-DD)")
        else:
            today_local = datetime.now(tz=LOCAL_TZ).date()
            if preferred_date < today_local:
                return jsonify({"fulfillmentText": "วันที่ที่เลือกเป็นอดีต กรุณาเลือกวันที่ในอนาคต"}), 200

        if not preferred_time: 
            missing.append("เวลา (รูปแบบ HH:MM เช่น 09:00 หรือ 'เช้า'/'บ่าย')")

        if not reason:
            missing.append("เหตุผลการนัด (สั้น ๆ)")

        phone_norm = normalize_phone_number(phone_raw) if phone_raw else None
        if phone_norm and not is_valid_thai_mobile(phone_norm):
            return jsonify({"fulfillmentText":  "เบอร์โทรที่ให้มาไม่ถูกต้อง กรุณาพิมพ์ใหม่เป็นตัวเลข 10 หลัก"}), 200

        if missing:
            ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยครับ"
            return jsonify({"fulfillmentText": ask}), 200

        pd_str = preferred_date.isoformat()
        pt_str = preferred_time

        ok = save_appointment_to_sheet(user_id, name, phone_norm, pd_str, pt_str, reason, status="New")
        if ok:
            notif = build_appointment_notification(user_id, pd_str, pt_str, reason)
            send_line_push(notif)
            return jsonify({"fulfillmentText": "รับเรื่องเรียบร้อยแล้ว ทีมพยาบาลจะติดต่อกลับเพื่อยืนยันวันเวลาค่ะ"}), 200
        else:
            return jsonify({"fulfillmentText":  "เกิดปัญหาในการบันทึก ขออภัย ลองใหม่อีกครั้งภายหลัง"}), 200

    # --- Symptom intent ---
    if intent == 'ReportSymptoms':
        res = calculate_symptom_risk(
            params.get('pain_score'),
            params.get('wound_status'),
            params.get('fever_check'),
            params.get('mobility_status')
        )
        return jsonify({"fulfillmentText": res}), 200

    # --- Personal risk ---
    elif intent == 'AssessPersonalRisk' or intent == 'AssessRisk':
        # Support both intent names for compatibility
        res = calculate_personal_risk(
            user_id,
            params.get('age'),
            params.get('weight'),
            params.get('height'),
            params.get('disease') or params.get('diseases')  # Support both parameter names
        )
        return jsonify({"fulfillmentText":  res}), 200

    elif intent == 'GetGroupID':
        return jsonify({"fulfillmentText": f"ID:  {os.environ.get('NURSE_GROUP_ID', 'Not Set')}"})

    # fallback - log unhandled intent for debugging
    logger.warning("Unhandled intent: %s with params: %s", intent, json.dumps(params, ensure_ascii=False))
    return jsonify({"fulfillmentText": f"ขอโทษค่ะ บอทยังไม่รองรับคำสั่ง '{intent}' ในขณะนี้"}), 200

# ---------- Run ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(port=port, debug=DEBUG)
