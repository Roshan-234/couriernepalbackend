from datetime import datetime
from app.extensions import db

class FAQ(db.Model):
    __tablename__ = 'faqs'
    id          = db.Column(db.Integer, primary_key=True)
    question    = db.Column(db.String(255), nullable=False)
    answer      = db.Column(db.Text, nullable=False)
    order       = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
