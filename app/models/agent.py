from datetime import datetime
from app.extensions import db

class AgentProfile(db.Model):
    __tablename__ = 'agent_profiles'
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    company_name   = db.Column(db.String(100), nullable=True)
    phone          = db.Column(db.String(20), nullable=True)
    address        = db.Column(db.String(255), nullable=True)
    onboarded_at   = db.Column(db.DateTime, default=datetime.utcnow)

    user           = db.relationship('User', back_populates='agent_profile')
    shipments      = db.relationship('Shipment', back_populates='agent')
