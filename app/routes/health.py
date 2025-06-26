# app/routes/health.py

from flask import Blueprint, jsonify
from sqlalchemy import text
from flask_mail import Message
from app.extensions import db, mail

health_ns = Blueprint("health", __name__)

@health_ns.route("/health", methods=["GET"])
def health_check():
    """Simple DB connectivity check."""
    try:
        # Use session.execute with a TextClause
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ok", "db": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "db_error": str(e)}), 500

@health_ns.route("/mail-test", methods=["GET"])
def mail_test():
    """Send a test email and report success/failure."""
    try:
        msg = Message(
            subject="Test Email from Moonlight Freight",
            recipients=["rosanghimire234@gmail.com"]
        )
        msg.body = "If you receive this, your SMTP is configured correctly!"
        mail.send(msg)
        return jsonify({"status": "ok", "mail": "sent"}), 200
    except Exception as e:
        return jsonify({"status": "error", "mail_error": str(e)}), 500
