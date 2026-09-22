from datetime import datetime
from app.extensions import db

class UserSession(db.Model):
    __tablename__ = "user_sessions"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    jti         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    ip_address  = db.Column(db.String(45), nullable=True)
    user_agent  = db.Column(db.String(500), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at  = db.Column(db.DateTime, nullable=True)
    is_revoked  = db.Column(db.Boolean, default=False, index=True)

    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref=db.backref("sessions", cascade="all, delete-orphan"),
    )

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "jti": self.jti,
            "ip_address": self.ip_address, "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat() if self.last_active else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_revoked": self.is_revoked,
        }
