# -*- coding: utf-8 -*-
"""
KwanNurse-Bot v3.0 - Perfect Core Features (Refactored)
Main Application Entry Point

3 Core Features:
  1. ReportSymptoms - AI ประเมินความเสี่ยงจากอาการ
  2. AssessRisk - ประเมินความเสี่ยงส่วนบุคคล
  3. RequestAppointment - จัดการนัดหมายพยาบาล

Refactored for maintainability and scalability.
"""
from flask import Flask
from config import PORT, DEBUG, get_logger
from routes import register_routes

# Initialize logger
logger = get_logger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['DEBUG'] = DEBUG

# Register all routes
register_routes(app)

# Log startup information
logger.info("=" * 60)
logger.info("KwanNurse-Bot v3.0 - Perfect Core (Refactored)")
logger.info("=" * 60)
logger.info("Debug Mode: %s", DEBUG)
logger.info("Features: ReportSymptoms, AssessRisk, RequestAppointment")
logger.info("=" * 60)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)
