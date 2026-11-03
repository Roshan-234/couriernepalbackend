from datetime import datetime
from app.extensions import db

class UserSession(db.Model):
    __tablename__ = "user_sessions"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    jti         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    #: The refresh token currently valid for this session. Rotated on every
    #: refresh; presenting a superseded one means the token was stolen and
    #: replayed, so the whole session is revoked rather than renewed.
    refresh_jti = db.Column(db.String(255), nullable=True, index=True)
    rotated_at  = db.Column(db.DateTime, nullable=True)
    #: Set when the session was killed by reuse detection rather than a logout,
    #: so an operator can tell a theft from an ordinary sign-out.
    revoked_reason = db.Column(db.String(60), nullable=True)
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

    def revoke(self, reason="logout"):
        self.is_revoked = True
        self.revoked_reason = reason

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "jti": self.jti,
            "ip_address": self.ip_address, "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat() if self.last_active else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
            "is_revoked": self.is_revoked,
            "revoked_reason": self.revoked_reason,
            "user_agent": self.user_agent,
        }
