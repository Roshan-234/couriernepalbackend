from datetime import datetime
from app.extensions import db

class NotificationLog(db.Model):
    __tablename__ = 'notification_logs'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    channel     = db.Column(db.String(20), nullable=False)   # EMAIL or SMS
    payload     = db.Column(db.Text)                         # JSON payload
    sent_at     = db.Column(db.DateTime, default=datetime.utcnow)
    status      = db.Column(db.String(50))                   # e.g. “SENT”, “FAILED”

    user        = db.relationship(
        'User',
        backref=db.backref('notifications', cascade='all, delete-orphan'),
    )
