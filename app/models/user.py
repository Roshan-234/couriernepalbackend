from datetime import datetime, timedelta
from enum import Enum
import secrets
from app.extensions import db, bcrypt
from sqlalchemy import String, DateTime

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
    password_reset_token = db.Column(String(128), nullable=True)
    password_reset_expires = db.Column(DateTime, nullable=True)
    
    # relationships
    roles          = db.relationship("Role", secondary="user_roles", backref="users")

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)
    
    def generate_reset_token(self):
        self.password_reset_token = secrets.token_urlsafe(32)
        self.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        return self.password_reset_token


    def to_dict(self):
        return {
            "id":         self.id,
            "username":   self.username,
            "email":      self.email,
            "first_name": self.first_name,
            "last_name":  self.last_name,
            "status":     self.status.value,
            "roles":      [r.name for r in self.roles]
        }
