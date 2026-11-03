from datetime import datetime
from app.extensions import db


class MerchantApiKey(db.Model):
    """A credential letting a merchant's own system call the shipping API.

    E-commerce sellers want to book shipments from their storefront, not from
    a browser session. A key is server-to-server: it is shown once at
    creation, stored only as a hash, scoped to the operations it may perform,
    and independently rate-limited so one noisy integration cannot starve the
    rest.
    """

    __tablename__ = 'merchant_api_keys'

    #: Operations a key may be granted. Deliberately narrow.
    SCOPES = (
        'shipments:read',
        'shipments:write',
        'tracking:read',
        'rates:read',
        'pickups:write',
        'cod:read',
        'webhooks:manage',
    )

    LIVE = 'live'
    TEST = 'test'

    id            = db.Column(db.Integer, primary_key=True)
    merchant_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name          = db.Column(db.String(120), nullable=False)
    environment   = db.Column(db.String(10), default=LIVE)

    #: Shown in the UI so a key can be recognised without revealing it.
    key_prefix    = db.Column(db.String(24), unique=True, nullable=False, index=True)
    key_hash      = db.Column(db.String(255), nullable=False)

    scopes        = db.Column(db.JSON, default=list)
    rate_limit_per_minute = db.Column(db.Integer, default=120)
    allowed_ips   = db.Column(db.JSON, default=list)    # empty means any

    is_active     = db.Column(db.Boolean, default=True, index=True)
    expires_at    = db.Column(db.DateTime)
    revoked_at    = db.Column(db.DateTime)
    revoked_by    = db.Column(db.Integer, db.ForeignKey('users.id'))

    last_used_at  = db.Column(db.DateTime)
    last_used_ip  = db.Column(db.String(64))
    use_count     = db.Column(db.Integer, default=0)

    created_by    = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    merchant = db.relationship('User', foreign_keys=[merchant_id])
    creator  = db.relationship('User', foreign_keys=[created_by])
    revoker  = db.relationship('User', foreign_keys=[revoked_by])

    @staticmethod
    def generate(environment=LIVE):
        """Mint a key. Returns ``(plaintext, prefix, hash)``.

        The plaintext is returned to the caller once and never stored.
        """
        import secrets
        from app.extensions import bcrypt
        body = secrets.token_urlsafe(32)
        prefix = f"mk_{environment}_{secrets.token_hex(4)}"
        plaintext = f"{prefix}.{body}"
        key_hash = bcrypt.generate_password_hash(plaintext).decode("utf-8")
        return plaintext, prefix, key_hash

    @staticmethod
    def prefix_of(plaintext):
        """Pull the lookup prefix out of a presented key."""
        if not isinstance(plaintext, str) or "." not in plaintext:
            return None
        return plaintext.split(".", 1)[0]

    def verify(self, plaintext):
        from app.extensions import bcrypt
        if not plaintext or not self.key_hash:
            return False
        return bcrypt.check_password_hash(self.key_hash, plaintext)

    @property
    def is_usable(self):
        if not self.is_active or self.revoked_at:
            return False
        if self.expires_at and self.expires_at <= datetime.utcnow():
            return False
        return True

    def allows(self, scope):
        return scope in (self.scopes or [])

    def allows_ip(self, ip):
        allowed = self.allowed_ips or []
        return not allowed or ip in allowed

    def to_dict(self):
        return {
            "id": self.id,
            "merchant_id": self.merchant_id,
            "name": self.name,
            "environment": self.environment,
            "key_prefix": self.key_prefix,
            "scopes": self.scopes or [],
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "allowed_ips": self.allowed_ips or [],
            "is_active": self.is_active,
            "is_usable": self.is_usable,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "last_used_ip": self.last_used_ip,
            "use_count": self.use_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
