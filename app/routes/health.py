from flask import Blueprint, jsonify, current_app
from sqlalchemy import text
from app.extensions import db

health_ns = Blueprint('health', __name__)

@health_ns.get('/health')
def health_check():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ok'), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Database readiness check failed')
        return jsonify(status='unavailable'), 503
