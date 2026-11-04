"""First-party analytics: what pages get read, without profiling anyone.

The site measured visits through Google Analytics. That is workable but it
sends European visitors' data to a US processor, which several supervisory
authorities have ruled against, and it makes the company a controller for a
transfer it does not benefit much from. Counting page views is not hard, and
doing it here keeps the data in one jurisdiction and under one retention rule.

Three things keep this proportionate:

- **No raw IP.** A salted hash is stored so a country can be derived and
  obvious bot traffic filtered, and nothing else.
- **A rotating visitor key.** Pseudonymous, first-party, and only written
  when the visitor has agreed to analytics. Without consent, an event is
  still counted but carries no visitor key at all, so it aggregates and
  cannot be strung into a journey.
- **Short retention.** Governed by a RetentionRule, defaulting to 90 days.
"""

from datetime import datetime

from app.extensions import db


class AnalyticsEvent(db.Model):
    """One thing that happened on the site."""

    __tablename__ = "analytics_events"
    __table_args__ = (
        db.Index("ix_analytics_occurred_name", "occurred_at", "name"),
        db.Index("ix_analytics_path_occurred", "path", "occurred_at"),
    )

    PAGE_VIEW = "page_view"
    QUOTE_REQUESTED = "quote_requested"
    TRACKING_LOOKUP = "tracking_lookup"
    SHIPMENT_BOOKED = "shipment_booked"
    CONTACT_SUBMITTED = "contact_submitted"
    AD_IMPRESSION = "ad_impression"
    AD_CLICK = "ad_click"
    NOTICE_SHOWN = "notice_shown"
    NOTICE_DISMISSED = "notice_dismissed"
    SUBSCRIBE = "newsletter_subscribe"
    OUTBOUND_CLICK = "outbound_click"

    NAMES = (PAGE_VIEW, QUOTE_REQUESTED, TRACKING_LOOKUP, SHIPMENT_BOOKED,
             CONTACT_SUBMITTED, AD_IMPRESSION, AD_CLICK, NOTICE_SHOWN,
             NOTICE_DISMISSED, SUBSCRIBE, OUTBOUND_CLICK)

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    name = db.Column(db.String(40), nullable=False, index=True)

    #: Null when the visitor has not consented to analytics: the event still
    #: counts towards totals but cannot be joined into a session.
    visitor_key = db.Column(db.String(64), nullable=True, index=True)
    session_key = db.Column(db.String(64), nullable=True, index=True)

    path = db.Column(db.String(300), index=True)
    referrer_host = db.Column(db.String(150))
    #: Campaign attribution, bounded and never free-form from the URL.
    utm_source = db.Column(db.String(60), index=True)
    utm_medium = db.Column(db.String(60))
    utm_campaign = db.Column(db.String(80), index=True)

    country = db.Column(db.String(2), index=True)
    device = db.Column(db.String(20))
    ip_hash = db.Column(db.String(64))

    #: Small, declared payload. Never arbitrary client data.
    detail = db.Column(db.JSON)
    #: Set for ad and notice events so reporting can join without a second table.
    ad_creative_id = db.Column(db.Integer, db.ForeignKey("ad_creatives.id"), index=True)
    notice_id = db.Column(db.Integer, db.ForeignKey("site_notices.id"), index=True)

    occurred_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "referrer_host": self.referrer_host,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "country": self.country,
            "device": self.device,
            "detail": self.detail,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
        }


class AnalyticsDaily(db.Model):
    """Pre-aggregated daily counts.

    Rolled up nightly so the dashboard does not scan the event table, and so
    the raw events can be purged on schedule while the trend survives. A
    rollup row holds no visitor key and is not personal data.
    """

    __tablename__ = "analytics_daily"
    __table_args__ = (
        db.UniqueConstraint("day", "name", "path", name="uq_analytics_daily_slice"),
    )

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    name = db.Column(db.String(40), nullable=False, index=True)
    path = db.Column(db.String(300), default="")
    events = db.Column(db.Integer, default=0)
    #: Distinct visitor keys seen. Only meaningful for consented traffic, so
    #: it is a floor rather than a true unique count - which is the honest
    #: trade for not tracking people who declined.
    visitors = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "day": self.day.isoformat() if self.day else None,
            "name": self.name,
            "path": self.path,
            "events": self.events,
            "visitors": self.visitors,
        }
