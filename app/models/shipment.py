from datetime import datetime
from app.extensions import db

class Shipment(db.Model):
    __tablename__ = 'shipments'
    id           = db.Column(db.Integer, primary_key=True)
    tracking_no  = db.Column(db.String(50), unique=True, nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    agent_id     = db.Column(db.Integer, db.ForeignKey('agent_profiles.id'))
    service_id   = db.Column(db.Integer, db.ForeignKey('services.id'))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    user         = db.relationship('User', backref='shipments')
    agent        = db.relationship('AgentProfile', back_populates='shipments')
    service      = db.relationship('Service', back_populates='shipments')
    parcels      = db.relationship('Parcel', back_populates='shipment')
