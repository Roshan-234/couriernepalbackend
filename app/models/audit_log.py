from datetime import datetime
from app.extensions import db

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action      = db.Column(db.String(100), nullable=False, index=True)
    resource    = db.Column(db.String(100), nullable=True, index=True)
    resource_id = db.Column(db.String(50), nullable=True)
    details     = db.Column(db.Text, nullable=True)
    ip_address  = db.Column(db.String(45), nullable=True)
    user_agent  = db.Column(db.String(500), nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    timestamp   = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", foreign_keys=[user_id], backref="audit_logs")

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id,
            "action": self.action, "resource": self.resource,
            "resource_id": self.resource_id, "details": self.details,
            "ip_address": self.ip_address, "status_code": self.status_code,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
