# -*- coding: utf-8 -*-
"""
Webhook Routes Module
Handles Dialogflow webhook endpoints
"""
import json
import os
from datetime import datetime
from flask import request, jsonify, Response
from config import get_logger, LOCAL_TZ, OFFICE_HOURS, DEBUG
from utils import (
    parse_date_iso,
    resolve_time_from_params,
    normalize_phone_number,
    is_valid_thai_mobile
)
from services import (
    calculate_symptom_risk,
    calculate_personal_risk,
    create_appointment,
    get_knowledge_menu,
    get_wound_care_guide,
    get_physical_therapy_guide,
    get_dvt_prevention_guide,
    get_medication_guide,
    get_warning_signs_guide,
    get_reminder_summary
)
from services.teleconsult import (
    is_office_hours,
    get_category_menu,
    parse_category_choice,
    start_teleconsult,
    cancel_consultation,
    get_queue_info_message,
    handle_after_hours_choice  # Bug #2 fix
)
from services.nlp import analyze_free_text, format_triage_message
from services.education import recommend_guides, format_recommendations_message
from services.notification import send_line_push
from services.metrics import incr
from services.security import require_line_signature, require_dialogflow_token
from database.education_logs import save_education_view
from config import NURSE_GROUP_ID

logger = get_logger(__name__)


def register_routes(app):
    """Register all webhook routes with Flask app"""
    
    @app.route('/', methods=['GET', 'HEAD'])
    def health_check():
        """Health check endpoint for monitoring services"""
        return jsonify({
            "status": "ok",
            "service": "KwanNurse-Bot v4.0",
            "version": "4.0 - Complete (6/6 Features)",
            "features": [
                "ReportSymptoms", 
                "AssessRisk", 
                "RequestAppointment", 
                "GetKnowledge",
                "FollowUpReminders",
                "Teleconsult"
            ],
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat()
        }), 200

    @app.route('/healthz', methods=['GET', 'HEAD'])
    def healthz():
        """
        Liveness probe สำหรับ UptimeRobot / Render health check.

        คืน plain text ``healthy`` ที่ **ต้องไม่ถูก compress** โดย Cloudflare
        เพราะ UptimeRobot free tier อ่าน body แบบ raw bytes เพื่อหา keyword
        — ถ้า body เป็น brotli compressed bytes, keyword "healthy" จะไม่เจอ.

        การทดลอง (2026-04-24) พบว่า Cloudflare เมิน ``Cache-Control: no-transform``
        และ compress body ขนาด 8 byte เป็น br ทันทีที่ client ส่ง
        ``Accept-Encoding: br`` (ซึ่ง UptimeRobot ทำ). วิธีแก้ที่ได้ผล:
        ส่ง header ``Content-Encoding: identity`` อย่างชัดเจน — ทำให้
        Cloudflare รู้ว่า response นี้ "encode แล้วเป็น identity" และ
        จะไม่ re-encode ซ้ำ.

        หมายเหตุ: endpoint นี้แยกจาก ``/`` ซึ่งตอบ JSON ยาวและโดน compress ได้
        โดยไม่กระทบ monitoring (เพราะ monitoring ใช้ /healthz เท่านั้น).
        """
        resp = Response("healthy\n", mimetype="text/plain")
        resp.headers["Cache-Control"] = "no-store, no-transform"
        # บังคับ Cloudflare ไม่ให้ compress (ดู docstring ด้านบน)
        resp.headers["Content-Encoding"] = "identity"
        resp.headers["Vary"] = "Accept-Encoding"
        return resp

    @app.route('/readyz', methods=['GET', 'HEAD'])
    def readyz():
        """
        Readiness probe (P4-3): unlike ``/healthz`` which only proves the
        process is alive, ``/readyz`` proves we can reach the dependencies
        a real request would need (Google Sheets).

        Use this for deploy gates / canary checks; do NOT point UptimeRobot
        at it because a transient Sheets blip should not look like the bot
        being down.

        Status code:
        - 200 when all probed deps are reachable
        - 503 when any dep fails (with JSON body listing which one)
        """
        from database.sheets import get_spreadsheet
        checks: dict = {}
        all_ok = True

        # Sheets probe — only if persistence is supposed to be available
        runtime_cfg = app.config.get('RUNTIME_CONFIG') or {}
        if runtime_cfg.get('can_persist'):
            try:
                ss = get_spreadsheet()
                checks["sheets"] = "ok" if ss is not None else "unavailable"
                if ss is None:
                    all_ok = False
            except Exception as exc:
                checks["sheets"] = f"error: {type(exc).__name__}"
                all_ok = False
        else:
            checks["sheets"] = "skipped (no credentials configured)"

        status_code = 200 if all_ok else 503
        return jsonify({
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat(),
        }), status_code

    @app.route('/metrics', methods=['GET'])
    def metrics_snapshot():
        """
        Lightweight in-process metrics snapshot (Phase 2 hardening).

        Poor-man's observability for single-node Render deploys. Counters
        reset on process restart; consume via uptime checks or scripts.
        Exposed without auth because no PII is stored here — only counter
        names and integer values.
        """
        from services.metrics import snapshot
        return jsonify({
            "timestamp": datetime.now(tz=LOCAL_TZ).isoformat(),
            "counters": snapshot(),
        }), 200

    @app.route('/webhook', methods=['POST'])
    @require_dialogflow_token
    def webhook():
        """Main Dialogflow webhook endpoint (P4-1: bearer token required when configured)."""
        req = request.get_json(silent=True, force=True)
        if not req:
            return jsonify({"fulfillmentText": "Request body empty"}), 400
        
        try:
            intent = req.get('queryResult', {}).get('intent', {}).get('displayName')
            params = req.get('queryResult', {}).get('parameters', {}) or {}
            user_id = req.get('session', 'unknown').split('/')[-1]
            query_text = req.get('queryResult', {}).get('queryText', '')
        except Exception:
            logger.exception("Error parsing request")
            return jsonify({
                "fulfillmentText": "เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้ง"
            }), 200
        
        # Log intent + masked user id. Avoid dumping full params (may contain
        # phone numbers, descriptions, or other PII). Full payload only in
        # DEBUG mode.
        masked_user = (user_id[:4] + "***" + user_id[-4:]) if len(user_id) > 10 else "***"
        if DEBUG:
            logger.info("Intent: %s | User: %s | Params: %s",
                       intent, masked_user, json.dumps(params, ensure_ascii=False))
        else:
            logger.info("Intent: %s | User: %s | ParamKeys: %s",
                       intent, masked_user, list(params.keys()))

        # P4-2: count every intent dispatch so /metrics surfaces traffic shape
        # and error rate. Errors caught here = handler raised; individual
        # handlers also have their own try/except for graceful user replies.
        intent_for_metric = (intent or "unknown").replace(".", "_")[:64]
        incr(f"webhook.intent.{intent_for_metric}")

        try:
            return _dispatch_intent(intent, user_id, params, query_text)
        except Exception:
            incr(f"webhook.error.{intent_for_metric}")
            logger.exception(
                "Unhandled exception in intent dispatch (intent=%s user=%s)",
                intent, masked_user,
            )
            return jsonify({
                "fulfillmentText": "ขอโทษค่ะ ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง"
            }), 200

    @app.route('/line/webhook', methods=['POST'])
    @require_line_signature
    def line_webhook():
        """
        Direct LINE Messaging API webhook (Sprint 2 S2-2; P4-1 hardened).

        Signature: every request must carry ``X-Line-Signature`` matching
        HMAC-SHA256 of the raw body keyed by ``LINE_CHANNEL_SECRET``. The
        ``@require_line_signature`` decorator enforces this when the secret
        is configured.

        Unlike ``/webhook`` which receives Dialogflow's intent-extracted
        format, this endpoint accepts the *raw* LINE event envelope
        (``{"events": [...]}``) so we can handle media messages — currently
        only images for wound analysis.

        Behavior:
        - 200 always (LINE retries on non-2xx; we should ack quickly even
          if individual events fail).
        - For each ``message`` event with ``message.type == 'image'``: run
          wound-image flow (download → analyze → save → reply + alert).
        - All other event types are ignored here — text messages should
          continue to flow through Dialogflow → ``/webhook`` as today.
        """
        body = request.get_json(silent=True) or {}
        events = body.get("events") or []
        for event in events:
            try:
                if event.get("type") != "message":
                    continue
                msg = event.get("message") or {}
                msg_type = msg.get("type")
                if msg_type == "image":
                    handle_line_image_event(event)
                elif msg_type == "audio":
                    # Phase 5 P5-2: voice → STT → triage pipeline
                    from services.voice import handle_voice_event
                    handle_voice_event(event)
            except Exception:
                logger.exception("Error processing LINE event: %s", event.get("type"))
        return jsonify({"status": "ok", "events_received": len(events)}), 200


def _dispatch_intent(intent, user_id, params, query_text):
    """
    Map a Dialogflow intent name to its handler. Extracted from the
    ``/webhook`` route so we can wrap dispatch in a single try/except
    + metric counter (P4-2).
    """
    if intent == 'ReportSymptoms':
        return handle_report_symptoms(user_id, params)
    elif intent == 'AssessPersonalRisk' or intent == 'AssessRisk':
        return handle_assess_risk(user_id, params)
    elif intent == 'RequestAppointment':
        return handle_request_appointment(user_id, params)
    elif intent == 'GetKnowledge':
        return handle_get_knowledge(user_id, params, query_text)
    elif intent == 'GetFollowUpSummary':
        return handle_get_followup_summary(user_id)
    elif intent == 'ContactNurse':
        return handle_contact_nurse(user_id, params, query_text)
    elif intent == 'AfterHoursChoice':
        # Bug #2 fix: รับคำตอบ 1/2 จากผู้ใช้หลังแสดงเมนูนอกเวลาทำการ
        result = handle_after_hours_choice(user_id, query_text)
        return jsonify({"fulfillmentText": result['message']}), 200
    elif intent == 'CancelConsultation':
        return handle_cancel_consultation(user_id)
    elif intent == 'GetGroupID':
        return handle_get_group_id()
    elif intent == 'FreeTextSymptom':
        return handle_free_text_symptom(user_id, params, query_text)
    elif intent == 'RecommendKnowledge':
        return handle_recommend_knowledge(user_id, params)
    else:
        return handle_unknown_intent(intent)


def handle_line_image_event(event):
    """
    Process a single LINE image event end-to-end.

    Steps:
    1. Download bytes from LINE Content API.
    2. Call ``services.wound_analysis.analyze_wound_image``.
    3. Persist to ``WoundAnalysisLog``.
    4. Reply to user (LINE Reply API).
    5. Push nurse alert if severity in {medium, high}.

    Never raises — all failures are logged and produce a fallback user reply.
    """
    # Local imports to keep webhook module load light + avoid circular imports
    from services.notification import (
        build_wound_alert_message,
        build_wound_user_reply,
        download_line_content,
        reply_line_message,
        send_line_push,
    )
    from services.wound_analysis import analyze_wound_image
    from database.wound_logs import save_wound_analysis

    source = event.get("source") or {}
    user_id = source.get("userId") or "unknown"
    reply_token = event.get("replyToken") or ""
    msg = event.get("message") or {}
    message_id = msg.get("id") or ""

    masked_user = (user_id[:4] + "***" + user_id[-4:]) if len(user_id) > 10 else "***"
    logger.info("LINE image event user=%s message_id=%s", masked_user, message_id)

    if not message_id:
        if reply_token:
            reply_line_message(reply_token, "ไม่พบรหัสรูปภาพ กรุณาส่งใหม่อีกครั้ง")
        return

    # 1. Download
    image_bytes = download_line_content(message_id)
    if not image_bytes:
        if reply_token:
            reply_line_message(
                reply_token,
                "ขออภัย ไม่สามารถดาวน์โหลดรูปได้ในขณะนี้\nกรุณาลองส่งใหม่ในอีกสักครู่",
            )
        return

    # 2. Analyze
    result = analyze_wound_image(image_bytes, mime_type="image/jpeg")
    if not result:
        # LLM disabled / failed / quota — friendly fallback to user
        if reply_token:
            reply_line_message(
                reply_token,
                "📸 ได้รับรูปแล้ว\nระบบ AI กำลังบำรุงรักษา พยาบาลจะตรวจสอบรูปและติดต่อกลับ",
            )
        # Still push to nurses with raw notice (no AI analysis)
        if NURSE_GROUP_ID:
            try:
                send_line_push(
                    f"📸 ผู้ป่วยส่งรูปแผล (AI ไม่พร้อม)\n👤 User: {user_id}\n"
                    f"กรุณาตรวจสอบรูปใน LINE",
                    NURSE_GROUP_ID,
                )
            except Exception:
                logger.exception("Failed to push raw wound notice")
        return

    # 3. Persist (best-effort — don't block user reply on Sheets failure)
    try:
        save_wound_analysis(
            user_id=user_id,
            severity=result["severity"],
            observations=result["observations"],
            advice=result["advice"],
            confidence=result["confidence"],
            image_size_kb=len(image_bytes) // 1024,
            message_id=message_id,
        )
    except Exception:
        logger.exception("Failed to persist wound analysis user=%s", user_id)

    # 4. Reply to user
    if reply_token:
        reply_line_message(
            reply_token,
            build_wound_user_reply(
                severity=result["severity"],
                observations=result["observations"],
                advice=result["advice"],
            ),
        )

    # 5. Alert nurse if medium or high
    if result["severity"] in ("medium", "high") and NURSE_GROUP_ID:
        try:
            send_line_push(
                build_wound_alert_message(
                    user_id=user_id,
                    severity=result["severity"],
                    observations=result["observations"],
                    advice=result["advice"],
                    confidence=result["confidence"],
                ),
                NURSE_GROUP_ID,
            )
        except Exception:
            logger.exception("Failed to push wound alert user=%s", user_id)


def handle_report_symptoms(user_id, params):
    """Handle ReportSymptoms intent"""
    pain = params.get('pain_score')
    wound = params.get('wound_status')
    fever = params.get('fever_check')
    mobility = params.get('mobility_status')
    # Phase 2-A: optional neuro branch (ชา / อ่อนแรง / ปวดร้าว).
    # Accepts several param aliases so it works whether Dialogflow exposes
    # it as `neuro_status`, `neuro`, or a generic `numbness` entity.
    neuro = (
        params.get('neuro_status')
        or params.get('neuro')
        or params.get('numbness')
    )

    # Validate required parameters
    missing = []
    if pain is None or str(pain).strip() == "":
        missing.append("ระดับความปวด (0-10)")
    if not wound:
        missing.append("สภาพแผล")
    if not fever:
        missing.append("อาการไข้")
    if not mobility:
        missing.append("การเคลื่อนไหว")

    if missing:
        ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยค่ะ"
        return jsonify({"fulfillmentText": ask}), 200

    # Calculate risk (neuro is optional so this stays backward-compatible)
    result = calculate_symptom_risk(user_id, pain, wound, fever, mobility, neuro=neuro)
    return jsonify({"fulfillmentText": result}), 200


def handle_assess_risk(user_id, params):
    """Handle AssessRisk intent"""
    age = params.get('age')
    weight = params.get('weight')
    height = params.get('height')
    disease = params.get('disease') or params.get('diseases')
    
    # Validate required parameters
    missing = []
    if age is None or str(age).strip() == "":
        missing.append("อายุ")
    if weight is None or str(weight).strip() == "":
        missing.append("น้ำหนัก (กิโลกรัม)")
    if height is None or str(height).strip() == "":
        missing.append("ส่วนสูง (เซนติเมตร)")
    if not disease:
        missing.append("โรคประจำตัว (หรือพิมพ์ 'ไม่มี')")
    
    if missing:
        ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยค่ะ"
        return jsonify({"fulfillmentText": ask}), 200
    
    # Calculate risk
    result = calculate_personal_risk(user_id, age, weight, height, disease)
    return jsonify({"fulfillmentText": result}), 200


def handle_request_appointment(user_id, params):
    """Handle RequestAppointment intent"""
    preferred_date_raw = (params.get('date') or 
                         params.get('preferred_date') or 
                         params.get('date-original'))
    preferred_time_raw = params.get('time') or params.get('preferred_time')
    timeofday_raw = params.get('timeofday') or params.get('time_of_day')
    reason = params.get('reason') or params.get('symptom') or params.get('description')
    name = params.get('name') or None
    phone_raw = params.get('phone-number') or params.get('phone') or None
    
    # Parse date and time
    preferred_date = parse_date_iso(preferred_date_raw)
    preferred_time = resolve_time_from_params(preferred_time_raw, timeofday_raw)
    
    # Validate required parameters
    missing = []
    
    if not preferred_date:
        missing.append("วันที่นัด (เช่น 25 มกราคม หรือ 2026-01-25)")
    else:
        # Check if date is in the past
        today_local = datetime.now(tz=LOCAL_TZ).date()
        if preferred_date < today_local:
            return jsonify({
                "fulfillmentText": "⚠️ วันที่ที่เลือกเป็นอดีตแล้ว กรุณาเลือกวันที่ในอนาคตค่ะ"
            }), 200
    
    if not preferred_time:
        missing.append("เวลานัด (เช่น 09:00 หรือ 'เช้า'/'บ่าย')")
    
    if not reason:
        missing.append("เหตุผลการนัด (เช่น เปลี่ยนผ้าพันแผล, ตรวจแผล)")
    
    # Validate phone if provided
    phone_norm = normalize_phone_number(phone_raw) if phone_raw else None
    if phone_norm and not is_valid_thai_mobile(phone_norm):
        return jsonify({
            "fulfillmentText": "⚠️ เบอร์โทรศัพท์ไม่ถูกต้อง กรุณาพิมพ์เป็นตัวเลข 10 หลัก (เช่น 0812345678)"
        }), 200
    
    if missing:
        ask = "กรุณาระบุ " + " และ ".join(missing) + " ด้วยค่ะ"
        return jsonify({"fulfillmentText": ask}), 200
    
    # Create appointment
    pd_str = preferred_date.isoformat()
    pt_str = preferred_time
    
    success, message = create_appointment(
        user_id, name, phone_norm, pd_str, pt_str, reason
    )
    
    return jsonify({"fulfillmentText": message}), 200


# Reverse map: display_name -> canonical key (used for EducationLog audit).
# Keep in sync with the values used in _KNOWLEDGE_TOPIC_MAP below.
_TOPIC_DISPLAY_TO_KEY = {
    'การดูแลแผล': 'wound_care',
    'กายภาพบำบัด': 'physical_therapy',
    'ป้องกันลิ่มเลือด': 'dvt_prevention',
    'การรับประทานยา': 'medication',
    'สัญญาณอันตราย': 'warning_signs',
}

# Map of topic keywords (Thai + English) → (display name, guide function).
# Used by ``handle_get_knowledge`` for both Dialogflow-extracted ``topic``
# parameters and as a substring fallback against ``query_text`` when the
# Dialogflow agent didn't annotate the ``KnowledgeTopic`` entity.
_KNOWLEDGE_TOPIC_MAP = {
    'wound_care': ('การดูแลแผล', get_wound_care_guide),
    'ดูแลแผล': ('การดูแลแผล', get_wound_care_guide),
    'การดูแลแผล': ('การดูแลแผล', get_wound_care_guide),
    'แผล': ('การดูแลแผล', get_wound_care_guide),

    'physical_therapy': ('กายภาพบำบัด', get_physical_therapy_guide),
    'กายภาพบำบัด': ('กายภาพบำบัด', get_physical_therapy_guide),
    'กายภาพ': ('กายภาพบำบัด', get_physical_therapy_guide),
    'ออกกำลังกาย': ('กายภาพบำบัด', get_physical_therapy_guide),

    'dvt': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),
    'dvt_prevention': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),
    'ลิ่มเลือด': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),
    'ป้องกันลิ่มเลือด': ('ป้องกันลิ่มเลือด', get_dvt_prevention_guide),

    'medication': ('การรับประทานยา', get_medication_guide),
    'ยา': ('การรับประทานยา', get_medication_guide),
    'ทานยา': ('การรับประทานยา', get_medication_guide),
    'รับประทานยา': ('การรับประทานยา', get_medication_guide),
    'วิธีทานยา': ('การรับประทานยา', get_medication_guide),

    'warning_signs': ('สัญญาณอันตราย', get_warning_signs_guide),
    'สัญญาณอันตราย': ('สัญญาณอันตราย', get_warning_signs_guide),
    'อาการอันตราย': ('สัญญาณอันตราย', get_warning_signs_guide),
    'อันตราย': ('สัญญาณอันตราย', get_warning_signs_guide),
    'เมื่อไหร่ต้องพบหมอ': ('สัญญาณอันตราย', get_warning_signs_guide),
}

# Words that mean "show me the menu" — bypass topic resolution.
_KNOWLEDGE_MENU_TRIGGERS = {'menu', 'เมนู', 'ความรู้', 'knowledge'}


def _resolve_knowledge_topic(text):
    """
    Find the best-matching knowledge topic for raw user text.

    Strategy:
    1. Exact match (case-insensitive) — fast path for Dialogflow-extracted
       single-word topics.
    2. Substring match — handles natural language like "อยากรู้เรื่องดูแลแผล".
       Returns the *longest* matching key so e.g. "ป้องกันลิ่มเลือด" wins
       over "ลิ่มเลือด" when the user typed the longer phrase.

    Returns:
        (topic_name, guide_func) tuple, or ``None`` if no match.
    """
    if not text:
        return None
    norm = str(text).lower().strip()
    if not norm:
        return None
    # 1. Exact match
    if norm in _KNOWLEDGE_TOPIC_MAP:
        return _KNOWLEDGE_TOPIC_MAP[norm]
    # 2. Substring — prefer longest key so multi-word phrases beat short ones
    matches = [
        (key, val) for key, val in _KNOWLEDGE_TOPIC_MAP.items()
        if key in norm
    ]
    if not matches:
        return None
    matches.sort(key=lambda kv: -len(kv[0]))
    return matches[0][1]


def handle_get_knowledge(user_id, params, query_text=""):
    """
    Handle GetKnowledge intent.

    Dialogflow ideally annotates the user's topic word as a ``topic`` /
    ``knowledge_topic`` parameter via the ``KnowledgeTopic`` entity. When
    the entity is missing or the agent failed to extract it, we fall back
    to scanning the raw ``query_text`` against the same keyword map. This
    keeps the bot useful even when Dialogflow training is incomplete.

    Side-effect: every successful guide delivery is logged to ``EducationLog``
    (Quick-win D3-A) so the nurse dashboard can show what topics the patient
    has been reading. Failures are swallowed — audit must never break replies.
    """
    topic_param = params.get('topic') or params.get('knowledge_topic')
    topic_str = str(topic_param).strip() if topic_param else ""

    # If user asked for the menu directly (or didn't ask for any topic),
    # show the menu — but only if query_text *also* doesn't carry a topic.
    if (not topic_str or topic_str.lower() in _KNOWLEDGE_MENU_TRIGGERS) and \
       (not query_text or query_text.lower().strip() in _KNOWLEDGE_MENU_TRIGGERS):
        result = get_knowledge_menu()
        return jsonify({"fulfillmentText": result}), 200

    # 1. Try Dialogflow-extracted topic first
    resolved = _resolve_knowledge_topic(topic_str) if topic_str else None
    # 2. Fallback to raw user text
    if resolved is None and query_text:
        resolved = _resolve_knowledge_topic(query_text)

    if resolved:
        topic_name, guide_func = resolved
        logger.info(
            "Knowledge request: %s (param=%r query=%r)",
            topic_name, topic_str, query_text,
        )
        # Audit: log topic view (best-effort, never raises)
        try:
            canonical = _TOPIC_DISPLAY_TO_KEY.get(topic_name, topic_name)
            save_education_view(
                user_id=user_id,
                topic=canonical,
                source="GetKnowledge",
                personalized=False,
            )
        except Exception:
            logger.exception("EducationLog write failed (non-fatal)")
        return jsonify({"fulfillmentText": guide_func()}), 200

    # Topic not found in either source
    shown = topic_str or query_text or ""
    return jsonify({
        "fulfillmentText": (
            f"ขอโทษค่ะ ไม่พบหัวข้อ '{shown}'\n\n"
            f"กรุณาพิมพ์ 'ความรู้' เพื่อดูหัวข้อที่มีค่ะ"
        )
    }), 200


def handle_get_followup_summary(user_id):
    """
    Handle GetFollowUpSummary intent
    FIXED: Added implementation for follow-up reminder summary
    
    Args:
        user_id: User's LINE ID
        
    Returns:
        JSON response with follow-up summary
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


def handle_get_group_id():
    """
    Handle GetGroupID debug intent.

    Guarded so production does NOT leak the nurse group id in responses.
    Enable by running with DEBUG=true (or env DEBUG=1).
    """
    if not DEBUG:
        logger.warning("GetGroupID invoked with DEBUG=false; refusing to expose group id")
        return jsonify({
            "fulfillmentText": "ฟีเจอร์นี้ปิดอยู่ในโหมดใช้งานจริง"
        }), 200

    group_id = os.environ.get('NURSE_GROUP_ID', 'Not Set')
    # Even in debug, show a truncated form to keep logs safer.
    shown = group_id if len(group_id) <= 10 else f"{group_id[:4]}***{group_id[-4:]}"
    return jsonify({
        "fulfillmentText": f"🔧 Debug Info:\nNURSE_GROUP_ID: {shown}"
    }), 200


def handle_contact_nurse(user_id, params, query_text):
    """
    Handle ContactNurse intent
    
    Manages the teleconsult flow including:
    - Category selection
    - Queue management
    - Office hours checking
    """
    try:
        logger.info(f"ContactNurse request from {user_id}")
        
        # Check if user provided category or description
        category_param = params.get('issue_category') or params.get('category')
        description_param = params.get('description') or params.get('issue_description')
        
        # If category is provided (or can be parsed from text)
        if category_param:
            issue_type = parse_category_choice(str(category_param))
        else:
            # Try to parse from query text
            issue_type = parse_category_choice(query_text)
        
        if issue_type:
            # Start teleconsult with the category
            description = str(description_param) if description_param else ""
            result = start_teleconsult(user_id, issue_type, description)
            
            return jsonify({"fulfillmentText": result['message']}), 200
        
        else:
            # No category yet, show menu
            menu = get_category_menu()
            
            # Add office hours info if outside hours
            if not is_office_hours():
                now = datetime.now(tz=LOCAL_TZ)
                current_time = now.strftime("%H:%M")
                
                menu = (
                    f"⏰ ขณะนี้นอกเวลาทำการ ({current_time} น.)\n"
                    f"เวลาทำการ: {OFFICE_HOURS['start']}-{OFFICE_HOURS['end']} น.\n\n"
                    f"{menu}\n\n"
                    f"💡 หากเป็นเรื่องฉุกเฉิน เลือกหมายเลข 1"
                )
            
            return jsonify({"fulfillmentText": menu}), 200
        
    except Exception as e:
        logger.exception(f"Error in ContactNurse: {e}")
        return jsonify({
            "fulfillmentText": "เกิดข้อผิดพลาด กรุณาลองใหม่ภายหลัง"
        }), 200


def handle_cancel_consultation(user_id):
    """Handle cancellation of consultation"""
    try:
        result = cancel_consultation(user_id)
        return jsonify({"fulfillmentText": result['message']}), 200
        
    except Exception as e:
        logger.exception(f"Error cancelling consultation: {e}")
        return jsonify({
            "fulfillmentText": "เกิดข้อผิดพลาดในการยกเลิก กรุณาลองใหม่"
        }), 200


def handle_free_text_symptom(user_id, params, query_text):
    """
    Handle FreeTextSymptom intent (Phase 2-E).

    Patient types an open-ended complaint (e.g. 'แผลมีน้ำเหลืองไหล ปวด 8/10').
    We run LLM + rule-based triage via services.nlp, reply with a structured
    summary, and escalate to the nurse group when risk is high.
    """
    try:
        text = (
            params.get('symptom_text')
            or params.get('description')
            or params.get('text')
            or query_text
            or ''
        )
        if not text or not str(text).strip():
            return jsonify({
                "fulfillmentText": (
                    "เล่าอาการให้ฟังหน่อยค่ะ เช่น\n"
                    "\"แผลบวมแดง ปวด 7/10 มีไข้นิดหน่อย เดินไม่ค่อยไหว\""
                )
            }), 200

        result = analyze_free_text(str(text))
        logger.info(
            "FreeTextSymptom triage: level=%s source=%s flags=%s",
            result.get('risk_level'), result.get('source'), result.get('flags'),
        )

        reply = format_triage_message(result)

        # Escalate to nurse group on high risk. Keep this best-effort: retry
        # logic already lives inside send_line_push.
        if result.get('risk_level') == 'high' and NURSE_GROUP_ID:
            try:
                flags = ", ".join(result.get('flags') or []) or "-"
                summary = result.get('summary') or "-"
                alert = (
                    "🚨 รายงานอาการจากแชต (เสี่ยงสูง)\n"
                    f"👤 ผู้ป่วย: {user_id}\n"
                    f"🔎 Flags: {flags}\n"
                    f"📋 สรุป: {summary}\n"
                    "กรุณาติดต่อกลับโดยเร็ว"
                )
                send_line_push(alert, NURSE_GROUP_ID)
            except Exception:
                logger.exception("Failed to send high-risk free-text alert")

        return jsonify({"fulfillmentText": reply}), 200

    except Exception:
        logger.exception("Error in FreeTextSymptom handler")
        return jsonify({
            "fulfillmentText": "ขอโทษค่ะ ระบบประเมินข้อความขัดข้อง กรุณาลองใหม่"
        }), 200


def handle_recommend_knowledge(user_id, params):
    """
    Handle RecommendKnowledge intent (Phase 2-C, refined in S2-3).

    Returns a personalized list of knowledge guides ordered by relevance to
    the patient's profile. Profile is assembled by
    ``services.patient_profile.get_or_build_profile`` which merges:

    1. Override fields from current Dialogflow params (highest priority)
    2. Stored sticky profile from ``PatientProfile`` sheet
    3. Latest demographics from ``RiskProfile`` (age + diseases)

    Newly discovered sticky fields (sex, surgery_type, surgery_date) are
    persisted back so future calls don't need to re-ask.
    """
    try:
        from services.patient_profile import get_or_build_profile
        profile = get_or_build_profile(user_id, params)
        recommendations = recommend_guides(profile, top_n=3)
        message = format_recommendations_message(recommendations)
        if not message:
            message = (
                "ตอนนี้ยังไม่มีคำแนะนำเฉพาะราย กรุณาพิมพ์ 'ความรู้' "
                "เพื่อดูเมนูทั้งหมดค่ะ"
            )
        logger.info(
            "RecommendKnowledge for %s: source=%s keys=%s",
            user_id,
            profile.get("source"),
            [r.get('key') for r in recommendations],
        )
        # Audit: log each recommendation (best-effort, never raises)
        try:
            for rec in recommendations:
                key = rec.get('key')
                if not key:
                    continue
                save_education_view(
                    user_id=user_id,
                    topic=key,
                    source="RecommendKnowledge",
                    personalized=True,
                )
        except Exception:
            logger.exception("EducationLog write failed (non-fatal)")
        return jsonify({"fulfillmentText": message}), 200

    except Exception:
        logger.exception("Error in RecommendKnowledge handler")
        return jsonify({
            "fulfillmentText": "ขอโทษค่ะ ไม่สามารถแนะนำความรู้ได้ในขณะนี้"
        }), 200


def handle_unknown_intent(intent):
    """Handle unknown/unhandled intents"""
    logger.warning("Unhandled intent: %s", intent)
    return jsonify({
        "fulfillmentText": (
            f"ขอโทษค่ะ บอทยังไม่รองรับคำสั่ง '{intent}' ในขณะนี้\n\n"
            f"คุณสามารถใช้ฟีเจอร์หลักได้:\n"
            f"• รายงานอาการ\n"
            f"• ประเมินความเสี่ยง\n"
            f"• นัดหมายพยาบาล\n"
            f"• ความรู้และคำแนะนำ (พิมพ์ 'แนะนำความรู้' สำหรับเฉพาะราย)\n"
            f"• ติดตามหลังจำหน่าย\n"
            f"• ปรึกษาพยาบาล\n"
            f"• เล่าอาการเป็นข้อความอิสระ"
        )
    }), 200
