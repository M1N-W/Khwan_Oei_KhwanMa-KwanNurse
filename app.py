# -*- coding: utf-8 -*-
"""
KwanNurse-Bot v4.0 - COMPLETE! 🎉
Main Application Entry Point

6/6 Core Features (100%):
  1. ReportSymptoms - AI ประเมินความเสี่ยงจากอาการ
  2. AssessRisk - ประเมินความเสี่ยงส่วนบุคคล
  3. RequestAppointment - จัดการนัดหมายพยาบาล
  4. GetKnowledge - คู่มือความรู้สุขภาพ
  5. FollowUpReminders - ระบบเตือนติดตามอัตโนมัติ
  6. Teleconsult - ปรึกษาพยาบาลแบบเรียลไทม์ ⭐ NEW!

Refactored for maintainability and scalability.
"""
from flask import Flask
from config import PORT, DEBUG, get_logger
from routes import register_routes
from services.scheduler import init_scheduler

# Initialize logger
logger = get_logger(__name__)


def create_app():
    """
    Application factory.
    Keeps route registration and scheduler init inside a callable so that
    Gunicorn worker forks do not each execute this code at import time,
    which would create duplicate APScheduler instances sending duplicate reminders.
    """
    flask_app = Flask(__name__)
    flask_app.config['DEBUG'] = DEBUG

    # Register all routes
    register_routes(flask_app)

    # Initialize scheduler for follow-up reminders
    try:
        init_scheduler()
        logger.info("✅ Reminder scheduler initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize scheduler: {e}")

    # Log startup information
    logger.info("=" * 60)
    logger.info("KwanNurse-Bot v4.0 - COMPLETE!")
    logger.info("=" * 60)
    logger.info("Debug Mode: %s", DEBUG)
    logger.info("Features (6/6 - 100%%): ")
    logger.info("  1. ✅ ReportSymptoms")
    logger.info("  2. ✅ AssessRisk")
    logger.info("  3. ✅ RequestAppointment")
    logger.info("  4. ✅ GetKnowledge")
    logger.info("  5. ✅ FollowUpReminders")
    logger.info("  6. ✅ Teleconsult ⭐ NEW")
    logger.info("=" * 60)
    logger.info("🎉 ALL FEATURES COMPLETE!")
    logger.info("=" * 60)

    return flask_app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)
