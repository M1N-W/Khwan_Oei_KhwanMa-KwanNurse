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
from config import PORT, DEBUG, get_logger, validate_runtime_config
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

    # Session secret key สำหรับ Nurse Dashboard (cookie signing).
    # ต้องกำหนด ``NURSE_DASHBOARD_SESSION_KEY`` ใน env ก่อนเปิด dashboard จริง
    # ถ้าไม่ได้ตั้งจะ fallback เป็นค่า random ต่อ process — หมายความว่า session
    # พยาบาลจะหลุดทุกครั้งที่ Render restart (ยอมรับได้ใน Sprint 1 แต่ production
    # ต้องตั้ง env ให้ชัด).
    flask_app.secret_key = (
        os.environ.get("NURSE_DASHBOARD_SESSION_KEY")
        or os.urandom(32)
    )
    # Cookie security: กัน XSS (HttpOnly) + กัน CSRF ข้าม origin (SameSite)
    # + ส่งเฉพาะ HTTPS เมื่อไม่ใช่ DEBUG
    flask_app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not DEBUG,
        # permanent session อายุ = idle timeout ขั้นสูงสุด
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24,  # 24 ชั่วโมงเป็นเพดาน (idle check ภายใน)
    )

    # Runtime config validation (non-fatal: log loudly, let health check reply 200)
    config_status = validate_runtime_config()
    flask_app.config['RUNTIME_CONFIG'] = config_status

    # Phase 4 P4-2: structured logging + request correlation IDs
    from services.observability import configure_logging, register_request_id_middleware
    configure_logging()
    register_request_id_middleware(flask_app)

    # Register all routes
    register_routes(flask_app)

    # ลงทะเบียน Nurse Dashboard blueprint (feature-flagged ภายใน route ด้วย auth module)
    from routes.dashboard import dashboard_bp
    flask_app.register_blueprint(dashboard_bp)

    # Scheduler ownership is now explicit so multi-worker deployments can
    # disable it on non-owner processes with RUN_SCHEDULER=false.
    # Also skip when persistence is not configured (scheduler would just log
    # repeated credential errors).
    if should_run_scheduler() and config_status.get('can_persist'):
        try:
            init_scheduler()
            logger.info("✅ Reminder scheduler initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize scheduler: {e}")
    elif not config_status.get('can_persist'):
        logger.warning(
            "ℹ️ Reminder scheduler skipped: Google Sheets credentials not configured"
        )
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

# Backward-compatible alias so deployments using `gunicorn app:app`
# (e.g. existing Render start command) keep working. Canonical WSGI
# entrypoint name remains `application`.
app = application


if __name__ == '__main__':
    # Local development only
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)
