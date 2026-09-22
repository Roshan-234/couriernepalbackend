from datetime import datetime
from app.extensions import db

class TrackingProvider(db.Model):
    __tablename__ = "tracking_providers"
    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(100), nullable=False)
    code              = db.Column(db.String(30), unique=True, nullable=False)
    api_url_template  = db.Column(db.String(500), nullable=True)
    api_key           = db.Column(db.String(255), nullable=True)
    webhook_url       = db.Column(db.String(500), nullable=True)
    logo_url          = db.Column(db.String(500), nullable=True)
    instructions      = db.Column(db.Text, nullable=True)
    is_active         = db.Column(db.Boolean, default=False)
    supported_countries = db.Column(db.String(500), default="all")
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_secrets=False):
        d = {
            "id": self.id, "name": self.name, "code": self.code,
            "logo_url": self.logo_url, "instructions": self.instructions,
            "is_active": self.is_active, "supported_countries": self.supported_countries,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_secrets:
            d["api_url_template"] = self.api_url_template
            d["api_key"] = self.api_key
            d["webhook_url"] = self.webhook_url
        return d
