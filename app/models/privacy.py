"""Data subject requests and the retention schedule.

GDPR gives people rights that a system has to be able to actually perform:
access (Art. 15), rectification (16), erasure (17), restriction (18),
portability (20) and objection (21). A controller has one month to respond,
extendable by two more for complex cases.

This models the request and its handling. It does not decide policy: what is
lawful to erase, what must be kept for tax or carriage liability, and who
signs off are the owner's decisions, and the retention table below is a
starting point to be reviewed rather than a ruling.
"""

from datetime import datetime, timedelta

from app.extensions import db


class DataRequest(db.Model):
    """A request from a person to exercise a right over their data."""

    __tablename__ = "data_requests"

    ACCESS = "access"
    RECTIFICATION = "rectification"
    ERASURE = "erasure"
    RESTRICTION = "restriction"
    PORTABILITY = "portability"
    OBJECTION = "objection"
    KINDS = (ACCESS, RECTIFICATION, ERASURE, RESTRICTION, PORTABILITY, OBJECTION)

    RECEIVED = "received"
    VERIFYING = "verifying"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REFUSED = "refused"
    STATUSES = (RECEIVED, VERIFYING, IN_PROGRESS, COMPLETED, REFUSED)

    #: One month to respond, extendable by two. Art. 12(3).
    RESPONSE_DAYS = 30

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(40), unique=True, nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(20), default=RECEIVED, index=True)

    #: Who is asking. A request can arrive from someone with no account, so
    #: the email is the handle and user_id is filled only if one matches.
    email = db.Column(db.String(200), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    subject_key = db.Column(db.String(64), nullable=True, index=True)

    details = db.Column(db.Text)
    #: Identity has to be established before data is handed over, or the
    #: process becomes a way to extract someone else's records.
    identity_verified_at = db.Column(db.DateTime, nullable=True)
    verification_note = db.Column(db.String(255))

    due_at = db.Column(db.DateTime, nullable=True, index=True)
    extended_until = db.Column(db.DateTime, nullable=True)
    extension_reason = db.Column(db.String(255))

    handled_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    outcome_note = db.Column(db.Text)
    #: Where the exported bundle was written, for an access or portability
    #: request. Never the data itself.
    export_path = db.Column(db.String(500))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subject = db.relationship("User", foreign_keys=[user_id])
    handler = db.relationship("User", foreign_keys=[handled_by])

    @staticmethod
    def generate_reference():
        from secrets import token_hex
        return f"DSR-{datetime.utcnow().strftime('%y%m')}-{token_hex(3).upper()}"

    def start_clock(self):
        self.due_at = datetime.utcnow() + timedelta(days=self.RESPONSE_DAYS)

    @property
    def deadline(self):
        return self.extended_until or self.due_at

    @property
    def is_overdue(self):
        deadline = self.deadline
        return bool(deadline and deadline < datetime.utcnow()
                    and self.status not in (self.COMPLETED, self.REFUSED))

    @property
    def days_remaining(self):
        deadline = self.deadline
        if not deadline or self.status in (self.COMPLETED, self.REFUSED):
            return None
        return (deadline - datetime.utcnow()).days

    def to_dict(self):
        return {
            "id": self.id,
            "reference": self.reference,
            "kind": self.kind,
            "status": self.status,
            "email": self.email,
            "user_id": self.user_id,
            "details": self.details,
            "identity_verified": self.identity_verified_at is not None,
            "identity_verified_at": self.identity_verified_at.isoformat() if self.identity_verified_at else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "extended_until": self.extended_until.isoformat() if self.extended_until else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "days_remaining": self.days_remaining,
            "is_overdue": self.is_overdue,
            "handled_by": self.handled_by,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "outcome_note": self.outcome_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RetentionRule(db.Model):
    """How long one kind of record is kept, and why.

    Storage limitation (Art. 5(1)(e)) means keeping personal data no longer
    than necessary. "Necessary" is a judgement the business makes, so these
    are rows an administrator can see and change rather than constants buried
    in code — and the reason is stored next to the period, because that is
    what an auditor asks for.
    """

    __tablename__ = "retention_rules"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    label = db.Column(db.String(150), nullable=False)
    #: The model or table the rule governs, for the purge job.
    target = db.Column(db.String(80), nullable=False)
    retain_days = db.Column(db.Integer, nullable=False)
    #: Why this period and not another: a legal obligation, a limitation
    #: period, or a business reason.
    basis = db.Column(db.Text)
    #: Off by default. Automatic deletion is not something to switch on for
    #: someone without their say-so.
    auto_purge = db.Column(db.Boolean, default=False)
    last_purged_at = db.Column(db.DateTime, nullable=True)
    last_purged_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "label": self.label,
            "target": self.target,
            "retain_days": self.retain_days,
            "basis": self.basis,
            "auto_purge": self.auto_purge,
            "last_purged_at": self.last_purged_at.isoformat() if self.last_purged_at else None,
            "last_purged_count": self.last_purged_count,
            "is_active": self.is_active,
        }


#: Starting schedule, seeded by `flask seed-privacy`. Every period here is a
#: suggestion the owner must confirm with their accountant and lawyer -
#: Nepali tax law, not GDPR, is what sets the floor on invoices and COD.
DEFAULT_RETENTION = [
    ("analytics_events", "Website analytics events", "AnalyticsEvent", 90,
     "Short, because the value is in trends rather than individual visits. "
     "Kept pseudonymous throughout."),
    ("ad_events", "Ad impressions and clicks", "AdEvent", 90,
     "Only needed long enough to report on a campaign."),
    ("consent_records", "Consent records", "ConsentRecord", 1095,
     "Evidence that consent was given, kept beyond the consent lifetime so it "
     "can still be produced after the fact. [TO CONFIRM with counsel]"),
    ("audit_logs", "Administrative audit log", "AuditLog", 365,
     "Security forensics. [TO CONFIRM: your own security policy may require longer]"),
    ("user_sessions", "Expired and revoked sessions", "UserSession", 90,
     "Useful for investigating account compromise, of no value after that."),
    ("marketing_contacts_unsubscribed", "Unsubscribed marketing contacts",
     "MarketingContact", 1095,
     "A suppression record has to outlive the subscription, or the person gets "
     "re-added by the next import. Keep the minimum that proves they opted out."),
    ("notification_logs", "Sent notification log", "NotificationLog", 180,
     "Delivery troubleshooting."),
    ("data_requests", "Data subject requests", "DataRequest", 1095,
     "Evidence that requests were answered within the deadline. "
     "[TO CONFIRM with counsel]"),
]
