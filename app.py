# -*- coding: utf-8 -*-
"""
KwanNurse-Bot v3.1 - With Follow-up Reminders (Refactored)
Main Application Entry Point

5 Core Features:
  1. ReportSymptoms - AI ประเมินความเสี่ยงจากอาการ
  2. AssessRisk - ประเมินความเสี่ยงส่วนบุคคล
  3. RequestAppointment - จัดการนัดหมายพยาบาล
  4. GetKnowledge - คู่มือความรู้สุขภาพ
  5. FollowUpReminders - ระบบเตือนติดตามอัตโนมัติ (NEW!)

Refactored for maintainability and scalability.
"""
from flask import Flask
from config import PORT, DEBUG, get_logger
from routes import register_routes
from services.scheduler import init_scheduler

# Initialize logger
logger = get_logger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['DEBUG'] = DEBUG

# Register all routes
register_routes(app)

# Initialize scheduler for follow-up reminders
try:
    init_scheduler()
    logger.info("✅ Reminder scheduler initialized successfully")
except Exception as e:
    logger.error(f"❌ Failed to initialize scheduler: {e}")

# Log startup information
logger.info("=" * 60)
logger.info("KwanNurse-Bot v3.1 - With Follow-up Reminders")
logger.info("=" * 60)
logger.info("Debug Mode: %s", DEBUG)
logger.info("Features:")
logger.info("  1. ReportSymptoms")
logger.info("  2. AssessRisk")
logger.info("  3. RequestAppointment")
logger.info("  4. GetKnowledge")
logger.info("  5. FollowUpReminders ⭐ NEW")
logger.info("=" * 60)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)
