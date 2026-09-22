from datetime import datetime
from app.extensions import db

class TrackingEvent(db.Model):
    __tablename__ = 'tracking_events'
    id           = db.Column(db.Integer, primary_key=True)
    shipment_id  = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    status       = db.Column(db.String(100), nullable=False)
    location     = db.Column(db.String(255))
    description  = db.Column(db.Text)
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow)

    shipment     = db.relationship('Shipment', back_populates='events')

    def to_dict(self):
        return {
            "id":          self.id,
            "status":      self.status,
            "location":    self.location,
            "description": self.description,
            "timestamp":   self.timestamp.isoformat(),
        }
