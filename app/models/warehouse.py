from datetime import datetime
from app.extensions import db

class Warehouse(db.Model):
    __tablename__ = 'warehouses'
    id          = db.Column(db.Integer, primary_key=True)
    code        = db.Column(db.String(20), unique=True, nullable=False)
    name        = db.Column(db.String(100), nullable=False)
    location    = db.Column(db.String(255))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    parcels     = db.relationship('Parcel', back_populates='warehouse')
