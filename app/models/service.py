from datetime import datetime
from app.extensions import db

class Service(db.Model):
    __tablename__ = 'services'
    id          = db.Column(db.Integer, primary_key=True)
    code        = db.Column(db.String(20), unique=True, nullable=False)  # e.g. “AIR_EXPRESS”
    name        = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    shipments   = db.relationship('Shipment', back_populates='service')
