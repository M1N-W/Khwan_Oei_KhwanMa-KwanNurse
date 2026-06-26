# -*- coding: utf-8 -*-
"""
Intent handlers for follow-up reminders and personalized recommendations (KWN-09).
"""
from flask import jsonify
from config import get_logger
from routes.webhook.helpers import _mask_user_id_for_log
from services import get_reminder_summary
from services.education import recommend_guides, format_recommendations_message
from database.education_logs import save_education_view

logger = get_logger(__name__)


def handle_get_followup_summary(user_id):
    """Handle GetFollowUpSummary intent"""
    try:
        logger.info(f"GetFollowUpSummary request from {user_id}")
        
        summary = get_reminder_summary(user_id)
        
        if 'error' in summary:
            return jsonify({
                "fulfillmentText": (
                    "ขอโทษค่ะ เกิดข้อผิดพลาดในการดึงข้อมูล\n"
                    "กรุณาลองใหม่อีกครั้งหรือติดต่อพยาบาลค่ะ"
                )
            }), 200
        
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
            
            if summary.get('latest'):
                latest = summary['latest']
                reminder_type = latest.get('Reminder_Type', 'unknown')
                status = latest.get('Status', 'unknown')
                timestamp = latest.get('Created_At', '')
                
                type_map = {
                    'day3': 'วันที่ 3',
                    'day7': 'วันที่ 7 (สัปดาห์แรก)',
                    'day14': 'วันที่ 14 (สัปดาห์ที่ 2)',
                    'day30': 'วันที่ 30 (ครบ 1 เดือน)'
                }
                type_display = type_map.get(reminder_type, reminder_type)
                
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


def handle_recommend_knowledge(user_id, params):
    """Handle RecommendKnowledge intent (Phase 2-C, refined in S2-3)."""
    try:
        from services.patient_profile import get_or_build_profile
        profile = get_or_build_profile(user_id, params)
        from routes.webhook import recommend_guides
        recommendations = recommend_guides(profile, top_n=3)
        message = format_recommendations_message(recommendations)
        if not message:
            message = (
                "ตอนนี้ยังไม่มีคำแนะนำเฉพาะราย กรุณาพิมพ์ 'ความรู้' "
                "เพื่อดูเมนูทั้งหมดค่ะ"
            )
        logger.info(
            "RecommendKnowledge for %s: source=%s keys=%s",
            _mask_user_id_for_log(user_id),
            profile.get("source"),
            [r.get('key') for r in recommendations],
        )
        try:
            from routes.webhook import save_education_view
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
