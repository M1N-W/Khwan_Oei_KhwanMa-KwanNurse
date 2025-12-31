# -*- coding: utf-8 -*-
"""
Khw anBot webhook (full, copy-paste ready)
Features:
 - Appointments (multi-param via Dialogflow) -> save to Google Sheet "Appointments"
 - Notify nurse group via LINE push (NURSE_GROUP_ID env var)
 - Symptom reporting (save to KhwanBot_Data.sheet1)
 - Personal risk assessment (RiskProfile worksheet)
 - Robust gspread auth (credentials.json or GSPREAD_CREDENTIALS env)
 - Dialogflow webhook endpoint
"""
from flask import Flask, request, jsonify
import gspread
from datetime import datetime
import os
import json
import requests
import logging
import re
from zoneinfo import ZoneInfo

# ---------- App config ----------
app = Flask(__name__)
DEBUG = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Bangkok")
WORKSHEET_LINK = os.environ.get("WORKSHEET_LINK", "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
NURSE_GROUP_ID = os.environ.get("NURSE_GROUP_ID")  # set this to group ID for push notifications

# ---------- gspread helper ----------
def get_sheet_client():
    """
    Return gspread client.
    Uses 'GSPREAD_CREDENTIALS' env var (JSON content) or credentials.json file if present.
    """
    try:
        creds_env = os.environ.get("GSPREAD_CREDENTIALS")
        if creds_env:
            creds_json = json.loads(creds_env)
            return gspread.service_account_from_dict(creds_json)
        if os.path.exists("credentials.json"):
            return gspread.service_account(filename="credentials.json")
        logger.warning("No Google credentials found (credentials.json or GSPREAD_CREDENTIALS).")
    except Exception:
        logger.exception("Connect Sheet Error")
    return None

# ---------- LINE push helper ----------
def send_line_push(message):
    """Push a text message to NURSE_GROUP_ID via LINE push API"""
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

# ---------- Appointment helpers ----------
def parse_date_iso(s: str):
    """Validate YYYY-MM-DD -> datetime.date or None"""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        # try to extract iso part if Dialogflow gave "2026-02-22T00:00:00Z"
        try:
            if "T" in s:
                return datetime.strptime(s.split("T")[0].strip(), "%Y-%m-%d").date()
        except Exception:
            return None
    return None

def parse_time_hhmm(s: str):
    """Validate HH:MM -> normalized string or None"""
    if not s:
        return None
    try:
        t = datetime.strptime(s.strip(), "%H:%M").time()
        return t.strftime("%H:%M")
    except Exception:
        # sometimes Dialogflow returns "09:00:00"
        try:
            if ":" in s:
                parts = s.split(":")
                return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        except Exception:
            return None
    return None

def save_appointment_to_sheet(user_id, name, phone, preferred_date, preferred_time, reason, status="New", assigned_to="", notes=""):
    """
    Append row to Google Sheet named 'Appointments' (sheet1).
    Columns: Timestamp | User_ID | Name | Phone | Preferred_Date | Preferred_Time | Reason | Status | Assigned_to | Notes
    """
    try:
        client = get_sheet_client()
        if not client:
            logger.error("No gspread client available.")
            return False
        # Open sheet named "Appointments" - ensure the sheet exists
        sheet = client.open("Appointments").sheet1
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
    return f"นัดใหม่ — user: {user_id}\nวันที่: {preferred_date} เวลา: {preferred_time}\nเรื่อง: {reason}\nดู sheet: {sheet_link}"

# ---------- Symptom & Personal Risk logic (full implementations) ----------
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
        notify_msg = f"🚨 DAILY REPORT (อาการแย่)\nRisk: {risk_score}\nPain: {pain}\nWound: {wound}\nกรุณาตรวจสอบทันที!"
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
        f"⚠️ ระดับ: {risk_level}\n"
        f"({desc})\n"
        f"💡 {advice}"
    )
    try:
        save_profile_data(user_id, age_val, weight_val, height_cm, bmi, disease_normalized, risk_level)
    except Exception:
        logger.exception("Error saving profile")
    if risk_score >= 4:
        notify_msg = f"🆕 ผู้ป่วยใหม่ (เสี่ยงสูง)\nUser: {user_id}\nอายุ {age_val}, โรค {diseases_str}\nโปรดวางแผนเยี่ยมบ้าน"
        send_line_push(notify_msg)
    return message

# ---------- Dialogflow webhook ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    if not req:
        return jsonify({"fulfillmentText": "Request body empty"}), 400
    try:
        intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
        params = req.get('queryResult', {}).get('parameters', {})
        original_req = req.get('originalDetectIntentRequest', {}) or {}
        # Fallback: use session id as user id if no richer payload
        user_id = req.get('session', 'unknown').split('/')[-1]
    except Exception:
        logger.exception("Parse Error")
        return jsonify({"fulfillmentText": "Error parsing request"}), 200

    logger.info("Intent incoming: %s user=%s", intent, user_id)

    # --- Appointment Intent ---
    if intent == 'RequestAppointment':
        # Dialogflow params: date, time, reason, name, phone
        preferred_date_raw = params.get('date') or params.get('preferred_date') or params.get('date-original')
        preferred_time_raw = params.get('time') or params.get('preferred_time')
        reason = params.get('reason') or params.get('symptom') or params.get('description')
        name = params.get('name') or None
        phone = params.get('phone-number') or params.get('phone') or None

        # Normalize date/time if provided
        preferred_date = None
        if isinstance(preferred_date_raw, str):
            preferred_date = parse_date_iso(preferred_date_raw)
        elif isinstance(preferred_date_raw, dict):
            # try to extract date-like value from dict
            raw_str = json.dumps(preferred_date_raw, ensure_ascii=False)
            m = re.search(r'(\d{4}-\d{2}-\d{2})', raw_str)
            if m:
                preferred_date = parse_date_iso(m.group(1))

        preferred_time = None
        if isinstance(preferred_time_raw, str):
            preferred_time = parse_time_hhmm(preferred_time_raw)

        # Ask for missing fields
        missing = []
        if not preferred_date:
            missing.append("วันที่ (รูปแบบ YYYY-MM-DD)")
        if not preferred_time:
            missing.append("เวลา (รูปแบบ HH:MM เช่น 09:00)")
        if not reason:
            missing.append("เหตุผลการนัด (สั้น ๆ)")

        if missing:
            ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยครับ"
            return jsonify({"fulfillmentText": ask}), 200

        # all required => save and notify
        pd_str = preferred_date.isoformat()
        pt_str = preferred_time
        ok = save_appointment_to_sheet(user_id, name, phone, pd_str, pt_str, reason, status="New")
        if ok:
            notif = build_appointment_notification(user_id, pd_str, pt_str, reason)
            send_line_push(notif)
            return jsonify({"fulfillmentText": "รับเรื่องเรียบร้อยแล้ว ทีมพยาบาลจะติดต่อกลับเพื่อยืนยันครับ"}), 200
        else:
            return jsonify({"fulfillmentText": "เกิดปัญหาในการบันทึก ขออภัย ลองใหม่อีกครั้งภายหลัง"}), 200

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
    elif intent == 'AssessPersonalRisk':
        res = calculate_personal_risk(
            user_id,
            params.get('age'),
            params.get('weight'),
            params.get('height'),
            params.get('disease')
        )
        return jsonify({"fulfillmentText": res}), 200

    elif intent == 'GetGroupID':
        return jsonify({"fulfillmentText": f"ID: {os.environ.get('NURSE_GROUP_ID', 'Not Set')}"})

    # fallback
    return jsonify({"fulfillmentText": "ขอโทษค่ะ บอทไม่เข้าใจคำสั่งนี้"}), 200

# ---------- Run ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(port=port, debug=DEBUG)
