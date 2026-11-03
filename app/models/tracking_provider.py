from datetime import datetime
from app.extensions import db
from app.crypto import EncryptedField, mask


class TrackingProvider(db.Model):
    """The original, thin carrier record: a public tracking URL and a key.

    Superseded by :class:`~app.models.carrier.CarrierConnector`, which carries
    a whole API connection, but still live at ``/api/tracking-providers``.

    The credential is held as ciphertext in ``api_key_encrypted`` and reached
    through the ``api_key`` descriptor, so a database dump no longer hands over
    every provider key. ``to_dict`` only ever emits a masked value; the
    plaintext is never serialized, matching ``CarrierConnector.to_dict``.
    """

    __tablename__ = "tracking_providers"

    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(100), nullable=False)
    code              = db.Column(db.String(30), unique=True, nullable=False)
    api_url_template  = db.Column(db.String(500), nullable=True)
    api_key_encrypted = db.Column(db.Text, nullable=True)
    webhook_url       = db.Column(db.String(500), nullable=True)
    logo_url          = db.Column(db.String(500), nullable=True)
    instructions      = db.Column(db.Text, nullable=True)
    is_active         = db.Column(db.Boolean, default=False)
    supported_countries = db.Column(db.String(500), default="all")
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    #: Plaintext in, ciphertext at rest.
    api_key = EncryptedField("api_key_encrypted")

    def to_dict(self, include_secrets=False):
        d = {
            "id": self.id, "name": self.name, "code": self.code,
            "logo_url": self.logo_url, "instructions": self.instructions,
            "is_active": self.is_active, "supported_countries": self.supported_countries,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_secrets:
            # Config the admin console needs, plus the credential rendered as
            # trailing characters only. The key itself never leaves the row.
            d["api_url_template"] = self.api_url_template
            d["webhook_url"] = self.webhook_url
            d["api_key_masked"] = mask(self.api_key_encrypted)
        return d
