from datetime import datetime
from app.extensions import db


class NotificationLog(db.Model):
    """One attempt to tell somebody something.

    A row is written for **every** attempt, including the ones that were
    deliberately not transmitted. That is the point of the table: until an
    SMS contract exists and the owner switches sending on, this log is the
    only evidence of what the system would have sent, and it is what makes
    the switch-on a reviewable decision rather than a leap.

    Two things about the shape are worth knowing.

    ``user_id`` is nullable, and that matters more than it looks. The person
    most interested in a delivery is usually the **receiver**, who has no
    account and never will — a relative in Doha waiting for a parcel. The
    original column was NOT NULL, which meant the one recipient who most
    wants the message could not even be recorded.

    ``recipient`` holds an email address or a phone number, so this table is
    personal data. It is covered by the ``notification_logs`` retention rule
    (180 days) rather than kept forever.
    """

    __tablename__ = 'notification_logs'

    #: Handed to a channel that accepted it.
    SENT = 'sent'
    #: Deliberately not transmitted — sending is off, or the channel is a
    #: seam with no provider behind it. Not a failure.
    SUPPRESSED = 'suppressed'
    #: Tried and refused: a bad address, a provider error.
    FAILED = 'failed'
    #: Nothing to do — no template, no address, or already told.
    SKIPPED = 'skipped'
    STATUSES = (SENT, SUPPRESSED, FAILED, SKIPPED)

    id          = db.Column(db.Integer, primary_key=True)
    #: Null whenever the recipient has no account, which is the common case
    #: for a consignee.
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'),
                            nullable=True, index=True)
    channel     = db.Column(db.String(20), nullable=False)
    payload     = db.Column(db.Text)                         # JSON payload
    sent_at     = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status      = db.Column(db.String(50), index=True)

    #: What happened, and which wording was used for it.
    event         = db.Column(db.String(60), index=True)
    template_code = db.Column(db.String(60), index=True)
    #: The address or number. Personal data; see the class docstring.
    recipient     = db.Column(db.String(200))
    #: Which party this was: 'sender', 'receiver' or 'account'.
    recipient_role = db.Column(db.String(20))
    shipment_id   = db.Column(db.Integer, db.ForeignKey('shipments.id'),
                              nullable=True, index=True)
    #: Why it was suppressed, skipped or failed, in words.
    detail        = db.Column(db.String(300))

    user        = db.relationship(
        'User',
        backref=db.backref('notifications', cascade='all, delete-orphan'),
    )
    shipment    = db.relationship('Shipment')

    @property
    def was_transmitted(self):
        """True only when something actually left the building."""
        return self.status == self.SENT

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "channel": self.channel,
            "status": self.status,
            "was_transmitted": self.was_transmitted,
            "event": self.event,
            "template_code": self.template_code,
            "recipient": self.recipient,
            "recipient_role": self.recipient_role,
            "shipment_id": self.shipment_id,
            "detail": self.detail,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }
