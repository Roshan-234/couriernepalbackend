from datetime import datetime
from app.extensions import db

class Parcel(db.Model):
    __tablename__ = 'parcels'
    id            = db.Column(db.Integer, primary_key=True)
    shipment_id   = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    warehouse_id  = db.Column(db.Integer, db.ForeignKey('warehouses.id'))
    weight_kg     = db.Column(db.Float, nullable=False)
    description   = db.Column(db.String(255))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    shipment      = db.relationship('Shipment', back_populates='parcels')
    warehouse     = db.relationship('Warehouse', back_populates='parcels')
    events        = db.relationship('TrackingEvent', back_populates='parcel')
