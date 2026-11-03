import hashlib
import secrets
from datetime import datetime, timedelta
from enum import Enum

from app.extensions import db, bcrypt
from sqlalchemy import String, DateTime

#: Failed sign-ins before an account is locked, and for how long. Locking the
#: account rather than the source address is what stops a distributed
#: password-spray, which per-IP limits cannot see.
MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15

class UserStatus(Enum):
    ACTIVE    = "active"
    INACTIVE  = "inactive"
    SUSPENDED = "suspended"
    PENDING   = "pending"

class User(db.Model):
    __tablename__ = "users"

    id             = db.Column(db.Integer, primary_key=True)
    username       = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email          = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash  = db.Column(db.String(255), nullable=False)
    first_name     = db.Column(db.String(50), nullable=False)
    last_name      = db.Column(db.String(50), nullable=False)
    status         = db.Column(db.Enum(UserStatus), default=UserStatus.PENDING, nullable=False)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    #: SHA-256 of the reset token, never the token itself. A database reader
    #: should not be able to take over an account with a live reset in flight.
    password_reset_token = db.Column(String(128), nullable=True, index=True)
    password_reset_expires = db.Column(DateTime, nullable=True)

    # Brute-force protection and account history
    failed_login_count = db.Column(db.Integer, default=0, nullable=False)
    locked_until       = db.Column(db.DateTime, nullable=True)
    last_login_at      = db.Column(db.DateTime, nullable=True)
    password_changed_at = db.Column(db.DateTime, nullable=True)
    #: Set on an account created by an administrator with a temporary password.
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)


    # relationships
    roles          = db.relationship("Role", secondary="user_roles", backref="users")
    agent_profile  = db.relationship("AgentProfile", back_populates="user", uselist=False)

    @property
    def is_admin(self):
        """True if the user holds an administrative role."""
        return any(r.name in ("super_admin", "admin") for r in self.roles)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        self.password_changed_at = datetime.utcnow()
        self.must_change_password = False

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    # ── Brute-force protection ───────────────────────────────────────────

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > datetime.utcnow())

    def record_failed_login(self):
        """Count a bad password and lock the account once it is clearly an attack."""
        self.failed_login_count = (self.failed_login_count or 0) + 1
        if self.failed_login_count >= MAX_FAILED_LOGINS:
            self.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        return self.is_locked

    def record_successful_login(self):
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = datetime.utcnow()

    # ── Password reset ───────────────────────────────────────────────────

    @staticmethod
    def hash_reset_token(token):
        """Tokens are looked up by digest, so the clear value is never stored."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def generate_reset_token(self):
        """Issue a reset token. The clear value is returned once, to be emailed."""
        token = secrets.token_urlsafe(32)
        self.password_reset_token = self.hash_reset_token(token)
        self.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        return token

    @classmethod
    def by_reset_token(cls, token):
        """Find the account a presented reset token belongs to, if still valid."""
        if not isinstance(token, str) or not token:
            return None
        user = cls.query.filter_by(password_reset_token=cls.hash_reset_token(token)).first()
        if not user or not user.password_reset_expires:
            return None
        if user.password_reset_expires < datetime.utcnow():
            return None
        return user

    def clear_reset_token(self):
        self.password_reset_token = None
        self.password_reset_expires = None


    def to_dict(self):
        return {
            "id":         self.id,
            "username":   self.username,
            "email":      self.email,
            "first_name": self.first_name,
            "last_name":  self.last_name,
            "status":     self.status.value,
            "roles":      [r.name for r in self.roles],
            "must_change_password": bool(self.must_change_password),
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }
