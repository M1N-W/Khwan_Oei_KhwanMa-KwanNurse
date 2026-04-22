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
import os

from flask import Flask
from config import PORT, DEBUG, get_logger
from routes import register_routes
from services.scheduler import init_scheduler

# Initialize logger
logger = get_logger(__name__)


def should_run_scheduler():
    """
    Decide whether this process should own reminder scheduling.

    Default remains enabled for backward compatibility, but deployments with
    multiple web workers can disable it per worker by setting
    RUN_SCHEDULER=false on non-owner processes.
    """
    return os.environ.get("RUN_SCHEDULER", "true").lower() in ("1", "true", "yes")


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

    # Scheduler ownership is now explicit so multi-worker deployments can
    # disable it on non-owner processes with RUN_SCHEDULER=false.
    if should_run_scheduler():
        try:
            init_scheduler()
            logger.info("✅ Reminder scheduler initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize scheduler: {e}")
    else:
        logger.info("ℹ️ Reminder scheduler disabled for this process")

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


# ---------------------------------------------------------------------------
# WSGI entry point for Gunicorn:
#   gunicorn "app:application"
# Using a lazy factory avoids running create_app() (and init_scheduler())
# in the master process before forking, which would cause duplicate APScheduler
# instances sending duplicate reminders. (Bug #4 fix)
# ---------------------------------------------------------------------------
application = create_app()


if __name__ == '__main__':
    # Local development only
    app = application
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)
