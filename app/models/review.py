"""Customer reviews, from two sources that must not be conflated.

**On-site reviews** are written by customers in our own form. We own them,
we moderate them, and we can reply to them.

**Google reviews** are somebody else's content, fetched from the Places
API and cached briefly. We may display them with attribution; we may not
edit them, present them as our own, or keep them indefinitely. They live
in their own table for that reason — one table with a `source` column
would have made it one careless query away from publishing Google's
content as ours, or from averaging the two into a single score.

Two decisions here are about trust rather than engineering, and both go
against what is convenient:

**Nothing is published automatically.** A new review is `pending` until a
person approves it. An unmoderated review wall on a twenty-year-old
company's site is a liability: the first thing it attracts is spam, and
the second is a competitor. The cost is a queue someone has to work; the
alternative is the company's reputation hosted on an open text box.

**Demo reviews are flagged and never counted.** Seeded examples exist so
the screens can be built and shown, and `is_demo` keeps them out of the
public listing and out of the aggregate rating. Marking up invented
reviews as real `AggregateRating` is against Google's structured data
policy and is, more simply, lying to customers.
"""

from datetime import datetime

from app.extensions import db


class Review(db.Model):
    """A review written by a customer on our own site."""

    __tablename__ = 'reviews'

    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    STATUSES = (PENDING, APPROVED, REJECTED)

    MIN_RATING = 1
    MAX_RATING = 5

    id = db.Column(db.Integer, primary_key=True)

    rating = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(150))
    body = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(5), default='en')

    #: Shown publicly. A first name and an initial is plenty; the form
    #: should not invite someone to publish their full identity.
    author_name = db.Column(db.String(120), nullable=False)
    #: Never shown publicly. Kept only so the office can reply or verify,
    #: and covered by the reviews retention rule.
    author_email = db.Column(db.String(200))
    author_country = db.Column(db.String(2))

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    #: Ties a review to a real delivery. This is what makes "verified
    #: customer" mean something rather than being a badge we hand out.
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipments.id'),
                            nullable=True, index=True)

    status = db.Column(db.String(20), default=PENDING, nullable=False, index=True)
    moderated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    moderated_at = db.Column(db.DateTime)
    #: Why it was rejected. For the office, never shown to the public.
    moderation_note = db.Column(db.String(300))

    #: The company's public answer, which is often worth more to a reader
    #: than the review itself.
    reply_body = db.Column(db.Text)
    reply_at = db.Column(db.DateTime)
    reply_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    #: Seeded example content. Excluded from the public listing and from
    #: every aggregate; see the module docstring.
    is_demo = db.Column(db.Boolean, default=False, nullable=False, index=True)

    ip_hash = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])
    moderator = db.relationship('User', foreign_keys=[moderated_by])
    replier = db.relationship('User', foreign_keys=[reply_by])
    shipment = db.relationship('Shipment')

    @property
    def is_verified_customer(self):
        """True only when the review is attached to a real shipment."""
        return self.shipment_id is not None

    @property
    def is_public(self):
        return self.status == self.APPROVED and not self.is_demo

    def approve(self, moderator_id):
        self.status = self.APPROVED
        self.moderated_by = moderator_id
        self.moderated_at = datetime.utcnow()
        self.moderation_note = None

    def reject(self, moderator_id, note=None):
        self.status = self.REJECTED
        self.moderated_by = moderator_id
        self.moderated_at = datetime.utcnow()
        self.moderation_note = (note or '')[:300] or None

    def to_dict(self, admin=False):
        payload = {
            'id': self.id,
            'rating': self.rating,
            'title': self.title,
            'body': self.body,
            'language': self.language,
            'author_name': self.author_name,
            'author_country': self.author_country,
            'is_verified_customer': self.is_verified_customer,
            'reply_body': self.reply_body,
            'reply_at': self.reply_at.isoformat() if self.reply_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if admin:
            payload.update({
                'status': self.status,
                'is_demo': self.is_demo,
                # Only an administrator sees who wrote it.
                'author_email': self.author_email,
                'user_id': self.user_id,
                'shipment_id': self.shipment_id,
                'moderated_by': self.moderated_by,
                'moderated_at': self.moderated_at.isoformat() if self.moderated_at else None,
                'moderation_note': self.moderation_note,
                'ip_hash': self.ip_hash,
            })
        return payload


class GoogleReview(db.Model):
    """A review fetched from the Google Places API and cached.

    Google's terms allow this content to be displayed with attribution and
    kept only briefly. It is therefore never edited here, never merged into
    our own ratings, and refreshed rather than accumulated: a fetch replaces
    what was stored for that place instead of adding to it.

    The Places API returns at most five reviews and gives no way to page
    through the rest, so this is a shop window, not a dataset.
    """

    __tablename__ = 'google_reviews'

    #: How long a cached copy may be served before it is refetched.
    MAX_AGE_HOURS = 24

    id = db.Column(db.Integer, primary_key=True)
    place_id = db.Column(db.String(200), nullable=False, index=True)

    #: Google's own identifier for the review where one is given, used to
    #: avoid duplicating a review across refreshes.
    external_id = db.Column(db.String(200), index=True)
    author_name = db.Column(db.String(200))
    author_url = db.Column(db.String(500))
    profile_photo_url = db.Column(db.String(500))
    rating = db.Column(db.Integer)
    text = db.Column(db.Text)
    #: Google's own phrasing, e.g. "a month ago". Reproduced rather than
    #: recalculated, because we are displaying their content.
    relative_time = db.Column(db.String(60))
    original_language = db.Column(db.String(10))
    posted_at = db.Column(db.DateTime)

    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'author_name': self.author_name,
            'author_url': self.author_url,
            'profile_photo_url': self.profile_photo_url,
            'rating': self.rating,
            'text': self.text,
            'relative_time': self.relative_time,
            'posted_at': self.posted_at.isoformat() if self.posted_at else None,
            'source': 'google',
        }


class PlaceRating(db.Model):
    """The overall rating Google holds for the business.

    Stored beside the reviews because the Places API returns it in the same
    call, and because it is the only honest way to show a Google score: it
    is Google's average over every review they hold, not an average of the
    five they let us display.
    """

    __tablename__ = 'place_ratings'

    id = db.Column(db.Integer, primary_key=True)
    place_id = db.Column(db.String(200), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255))
    rating = db.Column(db.Float)
    total_ratings = db.Column(db.Integer)
    url = db.Column(db.String(500))
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'place_id': self.place_id,
            'name': self.name,
            'rating': self.rating,
            'total_ratings': self.total_ratings,
            'url': self.url,
            'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None,
        }
