from datetime import datetime
from app.extensions import db

class ContactForm(db.Model):
    __tablename__ = 'contact_forms'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    email       = db.Column(db.String(120), nullable=False)
    subject     = db.Column(db.String(150))
    message     = db.Column(db.Text, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
