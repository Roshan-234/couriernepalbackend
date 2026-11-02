from datetime import datetime
from app.extensions import db


class SitePage(db.Model):
    """Structured editorial content; executable HTML/templates never come from the DB."""
    __tablename__ = 'site_pages'
    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(160), unique=True, nullable=False)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(320), nullable=False)
    eyebrow = db.Column(db.String(160), nullable=False, default='')
    heading = db.Column(db.String(200), nullable=False)
    intro = db.Column(db.Text, nullable=False, default='')
    sections = db.Column(db.JSON, nullable=False, default=list)
    published = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {key: getattr(self, key) for key in ('path', 'title', 'description', 'eyebrow', 'heading', 'intro', 'sections', 'published', 'version')} | {'updated_at': self.updated_at.isoformat()}
