"""Promotions, campaigns and site notices.

This is the company advertising its own offers on its own site — a Dashain
discount on cargo to the Gulf, a student excess-baggage promotion in August —
rather than a third-party ad network. That distinction matters: no external
script runs, nothing leaves the server, and a visitor who declines marketing
cookies can still be shown an offer. What they cannot be shown is one chosen
*because of* what they did before, which is the part that needs consent.
"""

from datetime import datetime

from app.extensions import db


class AdCampaign(db.Model):
    """A promotion with a reason, a period and a budget of attention."""

    __tablename__ = "ad_campaigns"

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    STATUSES = (DRAFT, SCHEDULED, RUNNING, PAUSED, FINISHED)

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    objective = db.Column(db.String(255))
    status = db.Column(db.String(20), default=DRAFT, index=True)

    starts_at = db.Column(db.DateTime, index=True)
    ends_at = db.Column(db.DateTime, index=True)

    #: Festival campaigns are the common case here, so the occasion is a
    #: first-class field rather than something buried in the name.
    occasion = db.Column(db.String(60))
    #: Where the traffic should land.
    landing_path = db.Column(db.String(300))
    #: Attribution appended to the landing link.
    utm_campaign = db.Column(db.String(80))

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creatives = db.relationship("AdCreative", back_populates="campaign",
                                cascade="all, delete-orphan")
    creator = db.relationship("User")

    def is_live(self, now=None):
        now = now or datetime.utcnow()
        if self.status != self.RUNNING:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    def to_dict(self, include_creatives=False):
        d = {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "objective": self.objective,
            "status": self.status,
            "occasion": self.occasion,
            "landing_path": self.landing_path,
            "utm_campaign": self.utm_campaign,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "is_live": self.is_live(),
            "creative_count": len(self.creatives),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_creatives:
            d["creatives"] = [c.to_dict() for c in self.creatives]
        return d


class AdPlacement(db.Model):
    """A named slot on the site that a creative can occupy.

    Slots are rows rather than hard-coded template positions so marketing can
    see what exists and what is filled, without a deployment.
    """

    __tablename__ = "ad_placements"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(300))
    #: Rough shape, so the editor can warn about a wrong-sized image.
    recommended_width = db.Column(db.Integer)
    recommended_height = db.Column(db.Integer)
    #: Only one creative shows at a time in a slot; highest priority wins.
    max_creatives = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)

    creatives = db.relationship("AdCreative", back_populates="placement")

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "name": self.name,
            "description": self.description,
            "recommended_width": self.recommended_width,
            "recommended_height": self.recommended_height,
            "max_creatives": self.max_creatives,
            "is_active": self.is_active,
        }


class AdCreative(db.Model):
    """The thing a visitor actually sees."""

    __tablename__ = "ad_creatives"

    BANNER = "banner"
    STRIP = "strip"
    CARD = "card"
    INLINE = "inline"
    FORMATS = (BANNER, STRIP, CARD, INLINE)

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("ad_campaigns.id"),
                            nullable=False, index=True)
    placement_id = db.Column(db.Integer, db.ForeignKey("ad_placements.id"),
                             nullable=False, index=True)

    format = db.Column(db.String(20), default=BANNER)
    headline = db.Column(db.String(150), nullable=False)
    body = db.Column(db.String(400))
    call_to_action = db.Column(db.String(60))
    target_path = db.Column(db.String(300))
    image_path = db.Column(db.String(500))
    #: Alt text is required for a published creative, not optional - an image
    #: that carries the offer is content, not decoration.
    image_alt = db.Column(db.String(200))
    #: Nepali variant, shown when the visitor is reading in Nepali.
    headline_ne = db.Column(db.String(200))
    body_ne = db.Column(db.String(500))

    #: Where it may appear. Empty means anywhere the placement exists.
    page_paths = db.Column(db.JSON, default=list)
    #: Audience by geography only. No behavioural targeting, which is what
    #: keeps this outside the consent requirement.
    countries = db.Column(db.JSON, default=list)

    priority = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, index=True)

    impressions = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    campaign = db.relationship("AdCampaign", back_populates="creatives")
    placement = db.relationship("AdPlacement", back_populates="creatives")

    @property
    def click_through_rate(self):
        if not self.impressions:
            return 0.0
        return round(self.clicks / self.impressions * 100, 2)

    def matches(self, path=None, country=None):
        paths = self.page_paths or []
        if paths and path and not any(path == p or path.startswith(p.rstrip("*"))
                                      for p in paths):
            return False
        countries = self.countries or []
        if countries and country and country.upper() not in [c.upper() for c in countries]:
            return False
        return True

    def to_dict(self, language="en"):
        nepali = language == "ne"
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "placement_id": self.placement_id,
            "placement": self.placement.code if self.placement else None,
            "format": self.format,
            "headline": (self.headline_ne if nepali and self.headline_ne else self.headline),
            "body": (self.body_ne if nepali and self.body_ne else self.body),
            "call_to_action": self.call_to_action,
            "target_path": self.target_path,
            "image_path": self.image_path,
            "image_alt": self.image_alt,
            "priority": self.priority,
            "is_active": self.is_active,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "click_through_rate": self.click_through_rate,
        }


class SiteNotice(db.Model):
    """An announcement shown as a bar or a dialog.

    Used for things people need to know rather than things being sold: a
    festival closure, a border disruption, a changed cut-off time. Frequency
    is capped and dismissal is remembered, because a notice that reappears on
    every page is how people learn to ignore them.
    """

    __tablename__ = "site_notices"

    BAR = "bar"
    MODAL = "modal"
    TOAST = "toast"
    STYLES = (BAR, MODAL, TOAST)

    INFO = "info"
    PROMOTION = "promotion"
    WARNING = "warning"
    DISRUPTION = "disruption"
    TONES = (INFO, PROMOTION, WARNING, DISRUPTION)

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text)
    title_ne = db.Column(db.String(200))
    body_ne = db.Column(db.Text)

    style = db.Column(db.String(20), default=BAR)
    tone = db.Column(db.String(20), default=INFO)

    link_path = db.Column(db.String(300))
    link_label = db.Column(db.String(60))

    starts_at = db.Column(db.DateTime, index=True)
    ends_at = db.Column(db.DateTime, index=True)
    page_paths = db.Column(db.JSON, default=list)

    #: A dismissed notice stays dismissed for this long. Zero means for the
    #: rest of the browsing session only.
    dismiss_days = db.Column(db.Integer, default=30)
    #: Shown at most this many times per visitor. Zero means no cap.
    max_impressions = db.Column(db.Integer, default=0)
    #: A disruption notice may need to be unmissable.
    is_dismissible = db.Column(db.Boolean, default=True)

    is_active = db.Column(db.Boolean, default=True, index=True)
    priority = db.Column(db.Integer, default=0)

    impressions = db.Column(db.Integer, default=0)
    dismissals = db.Column(db.Integer, default=0)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = db.relationship("User")

    def is_live(self, now=None):
        now = now or datetime.utcnow()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    def matches(self, path=None):
        paths = self.page_paths or []
        if not paths or not path:
            return True
        return any(path == p or path.startswith(p.rstrip("*")) for p in paths)

    def to_dict(self, language="en"):
        nepali = language == "ne"
        return {
            "id": self.id,
            "code": self.code,
            "title": (self.title_ne if nepali and self.title_ne else self.title),
            "body": (self.body_ne if nepali and self.body_ne else self.body),
            "style": self.style,
            "tone": self.tone,
            "link_path": self.link_path,
            "link_label": self.link_label,
            "dismiss_days": self.dismiss_days,
            "max_impressions": self.max_impressions,
            "is_dismissible": self.is_dismissible,
            "priority": self.priority,
            "is_active": self.is_active,
            "is_live": self.is_live(),
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "impressions": self.impressions,
            "dismissals": self.dismissals,
        }
