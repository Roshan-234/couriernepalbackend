"""Email marketing: subscribers, campaigns and sends.

Two rules shape this model, and both are legal rather than aesthetic.

**Consent has to be provable.** Under GDPR a pre-ticked box is not consent
(Art. 4(11), recital 32), so a subscription is not live until the person
confirms it from their own inbox. The confirmation token, the time it was
used and the address it came from are kept as the evidence.

**Unsubscribing has to work, and has to last.** An unsubscribed address is
not deleted: it is kept as a suppression record, because deleting it means
the next import silently re-subscribes them. That record is the minimum
needed to honour the objection and nothing more.
"""

import secrets
from datetime import datetime, timedelta

from app.extensions import db


class MarketingContact(db.Model):
    """Someone who has asked to hear from the company."""

    __tablename__ = "marketing_contacts"

    PENDING = "pending"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    STATUSES = (PENDING, SUBSCRIBED, UNSUBSCRIBED, BOUNCED, COMPLAINED)

    #: How long a pending double opt-in stays open before it is dropped.
    CONFIRM_WINDOW_HOURS = 72

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150))
    language = db.Column(db.String(5), default="en")
    country = db.Column(db.String(2))

    status = db.Column(db.String(20), default=PENDING, index=True)
    #: Free-form labels for segmentation: "merchant", "diaspora-uk", "student".
    tags = db.Column(db.JSON, default=list)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    # ── Evidence of consent ────────────────────────────────────────────
    source = db.Column(db.String(60))
    consent_text = db.Column(db.String(400))
    confirm_token = db.Column(db.String(64), index=True)
    confirm_sent_at = db.Column(db.DateTime)
    confirmed_at = db.Column(db.DateTime)
    confirm_ip_hash = db.Column(db.String(64))
    signup_ip_hash = db.Column(db.String(64))

    # ── Leaving ────────────────────────────────────────────────────────
    unsubscribe_token = db.Column(db.String(64), unique=True, index=True)
    unsubscribed_at = db.Column(db.DateTime)
    unsubscribe_reason = db.Column(db.String(200))
    bounced_at = db.Column(db.DateTime)
    bounce_count = db.Column(db.Integer, default=0)

    last_sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User")

    @staticmethod
    def new_token():
        return secrets.token_urlsafe(32)

    @property
    def is_mailable(self):
        """Only a confirmed subscriber may be sent a campaign."""
        return self.status == self.SUBSCRIBED and bool(self.confirmed_at)

    @property
    def confirmation_expired(self):
        if self.status != self.PENDING or not self.confirm_sent_at:
            return False
        return self.confirm_sent_at + timedelta(hours=self.CONFIRM_WINDOW_HOURS) < datetime.utcnow()

    def confirm(self, ip_hash=None):
        self.status = self.SUBSCRIBED
        self.confirmed_at = datetime.utcnow()
        self.confirm_ip_hash = ip_hash
        self.confirm_token = None
        if not self.unsubscribe_token:
            self.unsubscribe_token = self.new_token()

    def unsubscribe(self, reason=None):
        self.status = self.UNSUBSCRIBED
        self.unsubscribed_at = datetime.utcnow()
        self.unsubscribe_reason = (reason or "")[:200] or None

    def to_dict(self, include_evidence=False):
        d = {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "language": self.language,
            "country": self.country,
            "status": self.status,
            "tags": self.tags or [],
            "is_mailable": self.is_mailable,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "unsubscribed_at": self.unsubscribed_at.isoformat() if self.unsubscribed_at else None,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_evidence:
            d.update({
                "source": self.source,
                "consent_text": self.consent_text,
                "confirm_sent_at": self.confirm_sent_at.isoformat() if self.confirm_sent_at else None,
                "signup_ip_hash": self.signup_ip_hash,
                "confirm_ip_hash": self.confirm_ip_hash,
                "bounce_count": self.bounce_count,
                "unsubscribe_reason": self.unsubscribe_reason,
            })
        return d


class EmailCampaign(db.Model):
    """One mailing to a selected audience."""

    __tablename__ = "email_campaigns"

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SENT = "sent"
    CANCELLED = "cancelled"
    STATUSES = (DRAFT, SCHEDULED, SENDING, SENT, CANCELLED)

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    preheader = db.Column(db.String(200))
    body_en = db.Column(db.Text)
    body_ne = db.Column(db.Text)

    status = db.Column(db.String(20), default=DRAFT, index=True)
    #: Which contacts to include: tags, status and country.
    audience = db.Column(db.JSON, default=dict)

    ad_campaign_id = db.Column(db.Integer, db.ForeignKey("ad_campaigns.id"))
    utm_campaign = db.Column(db.String(80))

    scheduled_for = db.Column(db.DateTime, index=True)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    #: A send is approved by a person before it goes. Mailing the whole list
    #: is not something to trigger with one click.
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    recipients = db.Column(db.Integer, default=0)
    delivered = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    unsubscribed = db.Column(db.Integer, default=0)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sends = db.relationship("EmailSend", back_populates="campaign",
                            cascade="all, delete-orphan")
    approver = db.relationship("User", foreign_keys=[approved_by])
    creator = db.relationship("User", foreign_keys=[created_by])

    @property
    def is_approved(self):
        return self.approved_at is not None

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "subject": self.subject,
            "preheader": self.preheader,
            "status": self.status,
            "audience": self.audience or {},
            "ad_campaign_id": self.ad_campaign_id,
            "utm_campaign": self.utm_campaign,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "is_approved": self.is_approved,
            "approved_by": self.approved_by,
            "recipients": self.recipients,
            "delivered": self.delivered,
            "failed": self.failed,
            "unsubscribed": self.unsubscribed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class EmailSend(db.Model):
    """One campaign to one contact.

    Note what is absent: no open-tracking pixel and no click-rewriting. Both
    are processing that needs its own consent and its own disclosure, and
    neither is worth it for a company whose mailings are service updates and
    seasonal offers. Delivery success is recorded; reading behaviour is not.
    """

    __tablename__ = "email_sends"
    __table_args__ = (
        db.UniqueConstraint("campaign_id", "contact_id", name="uq_email_send_once"),
    )

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    STATUSES = (QUEUED, SENT, FAILED, SKIPPED)

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("email_campaigns.id"),
                            nullable=False, index=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("marketing_contacts.id"),
                           nullable=False, index=True)
    status = db.Column(db.String(20), default=QUEUED, index=True)
    error = db.Column(db.String(300))
    #: Why a contact was passed over, so a thin send can be explained.
    skip_reason = db.Column(db.String(100))
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaign = db.relationship("EmailCampaign", back_populates="sends")
    contact = db.relationship("MarketingContact")

    def to_dict(self):
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "contact_id": self.contact_id,
            "status": self.status,
            "error": self.error,
            "skip_reason": self.skip_reason,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }
