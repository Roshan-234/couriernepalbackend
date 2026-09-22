from datetime import datetime
from app.extensions import db

class Warehouse(db.Model):
    __tablename__ = 'warehouses'
    id             = db.Column(db.Integer, primary_key=True)
    code           = db.Column(db.String(20), unique=True, nullable=False)
    name           = db.Column(db.String(100), nullable=False)
    location       = db.Column(db.String(255))
    address        = db.Column(db.String(255))
    city           = db.Column(db.String(100))
    state          = db.Column(db.String(100))
    country        = db.Column(db.String(100), default='Nepal')
    postal_code    = db.Column(db.String(20))
    contact_person = db.Column(db.String(120))
    phone          = db.Column(db.String(50))
    email          = db.Column(db.String(120))
    is_active      = db.Column(db.Boolean, default=True, index=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    parcels     = db.relationship('Parcel', back_populates='warehouse')
