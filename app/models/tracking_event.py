from datetime import datetime
from app.extensions import db

class TrackingEvent(db.Model):
    __tablename__ = 'tracking_events'
    id           = db.Column(db.Integer, primary_key=True)
    parcel_id    = db.Column(db.Integer, db.ForeignKey('parcels.id'), nullable=False)
    status       = db.Column(db.String(100), nullable=False)  # e.g. “In Transit”
    location     = db.Column(db.String(255))
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow)

    parcel       = db.relationship('Parcel', back_populates='events')
