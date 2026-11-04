"""Consent records: the evidence side of cookie and marketing permission.

Consent was previously a value in the visitor's own `localStorage`. That is
enough to *honour* a choice but not to *demonstrate* one, and GDPR Article
7(1) puts the burden of demonstrating it on the controller. A visitor who
clears their browser takes the only evidence with them.

Each record is an immutable snapshot of one decision: which categories were
granted, under which version of the policy, when, and enough technical
context to tie it to a session without storing anything that identifies a
person on its own. Changing a preference writes a new record rather than
editing the old one, so the history is the audit trail.

What is deliberately NOT stored: the raw IP address. It is personal data, it
is not needed to prove consent, and keeping it would make this table a
liability rather than a defence. A salted hash is kept instead, which can
confirm "this is the same visitor" without revealing who.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from app.extensions import db

#: Cookie and processing categories offered to a visitor.
#:
#: `necessary` is listed for completeness and is never optional: it covers the
#: session cookie, the CSRF token and the consent record itself, all of which
#: are strictly required to deliver a service the user asked for and so fall
#: outside the consent requirement in the ePrivacy Directive.
NECESSARY = "necessary"
ANALYTICS = "analytics"
MARKETING = "marketing"
PREFERENCES = "preferences"

CATEGORIES = (NECESSARY, ANALYTICS, MARKETING, PREFERENCES)
OPTIONAL_CATEGORIES = (ANALYTICS, MARKETING, PREFERENCES)

CATEGORY_LABELS = {
    NECESSARY: "Strictly necessary",
    ANALYTICS: "Analytics",
    MARKETING: "Marketing and advertising",
    PREFERENCES: "Preferences",
}

CATEGORY_DESCRIPTIONS = {
    NECESSARY: (
        "Keeps you signed in, protects forms against cross-site request forgery, "
        "and remembers this choice. Cannot be switched off."
    ),
    ANALYTICS: (
        "Counts visits and which pages are read, so we can see what is useful. "
        "Measured on our own servers; no profile is built and nothing is sold."
    ),
    MARKETING: (
        "Lets us show offers and measure whether a campaign worked."
    ),
    PREFERENCES: (
        "Remembers choices such as language or a dismissed notice."
    ),
}

#: Bump when the wording or the purposes change. A visitor whose stored
#: version is older is asked again rather than being assumed to agree to
#: something they never saw.
CURRENT_POLICY_VERSION = "2026-11-03.1"

#: How long a consent decision stands before it is sought again. The EDPB has
#: no fixed figure; several supervisory authorities point at 6-12 months.
CONSENT_LIFETIME_DAYS = 180


def hash_identifier(value, salt=None):
    """Salted SHA-256 of an IP or similar, so it can be matched but not read.

    The salt comes from ``CONSENT_HASH_SALT`` and falls back to ``SECRET_KEY``.
    Without a salt an IP hash is trivially reversible by brute force, since
    the whole IPv4 space is small.
    """
    if not value:
        return None
    salt = salt or os.getenv("CONSENT_HASH_SALT") or os.getenv("SECRET_KEY") or "dev-salt"
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


class ConsentRecord(db.Model):
    """One consent decision, kept as written. Never updated in place."""

    __tablename__ = "consent_records"

    GIVEN = "given"
    UPDATED = "updated"
    WITHDRAWN = "withdrawn"
    SOURCES = ("banner", "preferences", "api", "import")

    id = db.Column(db.Integer, primary_key=True)

    #: First-party pseudonymous id held in a cookie. Not linked to an account
    #: unless the visitor signs in, in which case user_id is filled too.
    subject_key = db.Column(db.String(64), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    #: The decision itself.
    categories = db.Column(db.JSON, nullable=False, default=list)
    action = db.Column(db.String(20), default=GIVEN, index=True)
    source = db.Column(db.String(20), default="banner")
    policy_version = db.Column(db.String(40), nullable=False)

    #: Evidence, kept deliberately thin.
    ip_hash = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    country = db.Column(db.String(2), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")

    @staticmethod
    def new_subject_key():
        return secrets.token_urlsafe(24)

    @staticmethod
    def normalise(categories):
        """Keep only recognised optional categories, always including necessary."""
        chosen = {c for c in (categories or []) if c in OPTIONAL_CATEGORIES}
        return [NECESSARY] + sorted(chosen)

    @classmethod
    def record(cls, subject_key, categories, *, action=GIVEN, source="banner",
               user_id=None, ip=None, user_agent=None, country=None):
        entry = cls(
            subject_key=subject_key,
            user_id=user_id,
            categories=cls.normalise(categories),
            action=action,
            source=source if source in cls.SOURCES else "api",
            policy_version=CURRENT_POLICY_VERSION,
            ip_hash=hash_identifier(ip),
            user_agent=(user_agent or "")[:300] or None,
            country=(country or None),
            expires_at=datetime.utcnow() + timedelta(days=CONSENT_LIFETIME_DAYS),
        )
        db.session.add(entry)
        return entry

    @classmethod
    def current_for(cls, subject_key):
        """The decision in force for a visitor, or None if it must be asked again."""
        if not subject_key:
            return None
        latest = (cls.query.filter_by(subject_key=subject_key)
                  .order_by(cls.created_at.desc(), cls.id.desc()).first())
        if not latest:
            return None
        if latest.policy_version != CURRENT_POLICY_VERSION:
            return None
        if latest.expires_at and latest.expires_at <= datetime.utcnow():
            return None
        if latest.action == cls.WITHDRAWN:
            return latest
        return latest

    def allows(self, category):
        if category == NECESSARY:
            return True
        if self.action == self.WITHDRAWN:
            return False
        return category in (self.categories or [])

    def to_dict(self, include_evidence=False):
        d = {
            "id": self.id,
            "categories": self.categories or [],
            "action": self.action,
            "source": self.source,
            "policy_version": self.policy_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
        if include_evidence:
            d.update({
                "subject_key": self.subject_key,
                "user_id": self.user_id,
                "country": self.country,
                "user_agent": self.user_agent,
                # The hash is shown, never anything reversible to an address.
                "ip_hash": self.ip_hash,
            })
        return d
