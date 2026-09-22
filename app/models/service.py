from datetime import datetime
from app.extensions import db

class Service(db.Model):
    __tablename__ = 'services'
    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(30), unique=True, nullable=False)
    name          = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.Text)
    delivery_time = db.Column(db.String(50))
    price_range   = db.Column(db.String(50))
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    shipments     = db.relationship('Shipment', back_populates='service')
