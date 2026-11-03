from datetime import datetime
from app.extensions import db


class RateCard(db.Model):
    """A priced service offering, valid for a period and optionally a merchant.

    ``PricingRule`` could only express a country pair, so domestic Nepal had no
    rates at all and every merchant paid list price. A rate card carries the
    rules that apply across a whole tariff (volumetric divisor, rounding, fuel,
    VAT) and owns the per-lane entries.

    A card with a ``merchant_id`` is a contract rate and beats the default.
    """

    __tablename__ = 'rate_cards'

    DOMESTIC = 'domestic'
    INTERNATIONAL = 'international'
    SCOPES = (DOMESTIC, INTERNATIONAL)

    id              = db.Column(db.Integer, primary_key=True)
    code            = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name            = db.Column(db.String(150), nullable=False)
    scope           = db.Column(db.String(20), default=DOMESTIC, index=True)
    service_level   = db.Column(db.String(30), default='standard', index=True)
    currency        = db.Column(db.String(10), default='NPR')

    merchant_id     = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    is_default      = db.Column(db.Boolean, default=False)
    is_active       = db.Column(db.Boolean, default=True, index=True)
    effective_from  = db.Column(db.Date)
    effective_to    = db.Column(db.Date)

    # Tariff-wide rules
    volumetric_divisor      = db.Column(db.Integer, default=5000)
    min_chargeable_weight_kg = db.Column(db.Float, default=0.5)
    weight_step_kg          = db.Column(db.Float, default=0.5)
    fuel_surcharge_percent  = db.Column(db.Float, default=0.0)
    vat_percent             = db.Column(db.Float, default=13.0)
    cod_fee_percent         = db.Column(db.Float, default=0.0)
    cod_fee_min             = db.Column(db.Float, default=0.0)
    insurance_percent       = db.Column(db.Float, default=0.0)

    notes           = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    merchant = db.relationship('User')
    entries  = db.relationship('RateCardEntry', back_populates='rate_card',
                               cascade='all, delete-orphan',
                               order_by='RateCardEntry.min_weight_kg')

    def is_effective(self, on_date=None):
        day = on_date or datetime.utcnow().date()
        if not self.is_active:
            return False
        if self.effective_from and day < self.effective_from:
            return False
        if self.effective_to and day > self.effective_to:
            return False
        return True

    def to_dict(self, include_entries=False):
        d = {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "scope": self.scope,
            "service_level": self.service_level,
            "currency": self.currency,
            "merchant_id": self.merchant_id,
            "is_default": self.is_default,
            "is_active": self.is_active,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "volumetric_divisor": self.volumetric_divisor,
            "min_chargeable_weight_kg": self.min_chargeable_weight_kg,
            "weight_step_kg": self.weight_step_kg,
            "fuel_surcharge_percent": self.fuel_surcharge_percent,
            "vat_percent": self.vat_percent,
            "cod_fee_percent": self.cod_fee_percent,
            "cod_fee_min": self.cod_fee_min,
            "insurance_percent": self.insurance_percent,
            "notes": self.notes,
            "entry_count": len(self.entries),
        }
        if include_entries:
            d["entries"] = [e.to_dict() for e in self.entries]
        return d


class RateCardEntry(db.Model):
    """One priced lane and weight band within a rate card.

    A lane is matched from the most specific key available: an exact district
    pair, then a zone pair, then a destination country. Zones come from
    ``app/data/nepal_geo.py``.
    """

    __tablename__ = 'rate_card_entries'
    __table_args__ = (
        db.Index('ix_rate_entry_lane', 'rate_card_id', 'origin_zone', 'destination_zone'),
    )

    id              = db.Column(db.Integer, primary_key=True)
    rate_card_id    = db.Column(db.Integer, db.ForeignKey('rate_cards.id'),
                                nullable=False, index=True)

    # Lane. Leave the narrower fields null to price a whole zone or country.
    origin_zone         = db.Column(db.String(20), index=True)
    destination_zone    = db.Column(db.String(20), index=True)
    origin_district     = db.Column(db.String(60))
    destination_district = db.Column(db.String(60))
    destination_country = db.Column(db.String(100), index=True)

    # Weight band, in chargeable kilograms
    min_weight_kg   = db.Column(db.Float, default=0.0)
    max_weight_kg   = db.Column(db.Float)

    # Price
    base_price      = db.Column(db.Float, default=0.0)
    price_per_kg    = db.Column(db.Float, default=0.0)
    min_charge      = db.Column(db.Float, default=0.0)

    transit_days_min = db.Column(db.Integer)
    transit_days_max = db.Column(db.Integer)
    is_active       = db.Column(db.Boolean, default=True)

    rate_card = db.relationship('RateCard', back_populates='entries')

    @property
    def specificity(self):
        """How narrowly this entry targets a lane. Higher wins."""
        score = 0
        if self.origin_district:
            score += 4
        if self.destination_district:
            score += 4
        if self.origin_zone:
            score += 2
        if self.destination_zone:
            score += 2
        if self.destination_country:
            score += 1
        return score

    def covers_weight(self, weight_kg):
        if weight_kg < (self.min_weight_kg or 0.0):
            return False
        if self.max_weight_kg is not None and weight_kg > self.max_weight_kg:
            return False
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "rate_card_id": self.rate_card_id,
            "origin_zone": self.origin_zone,
            "destination_zone": self.destination_zone,
            "origin_district": self.origin_district,
            "destination_district": self.destination_district,
            "destination_country": self.destination_country,
            "min_weight_kg": self.min_weight_kg,
            "max_weight_kg": self.max_weight_kg,
            "base_price": self.base_price,
            "price_per_kg": self.price_per_kg,
            "min_charge": self.min_charge,
            "transit_days_min": self.transit_days_min,
            "transit_days_max": self.transit_days_max,
            "is_active": self.is_active,
        }


class Surcharge(db.Model):
    """An extra charge applied on top of the lane price."""

    __tablename__ = 'surcharges'

    FLAT = 'flat'
    PERCENT = 'percent'
    PER_KG = 'per_kg'
    CALCULATIONS = (FLAT, PERCENT, PER_KG)

    #: What has to be true of a shipment for the charge to apply.
    APPLIES = ('all', 'domestic', 'international', 'cod', 'fragile',
               'remote', 'insurance', 'oversize', 'weekend', 'fuel')

    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name          = db.Column(db.String(150), nullable=False)
    description   = db.Column(db.Text)
    applies_to    = db.Column(db.String(30), default='all', index=True)
    calculation   = db.Column(db.String(20), default=FLAT)
    value         = db.Column(db.Float, default=0.0)
    min_amount    = db.Column(db.Float, default=0.0)
    max_amount    = db.Column(db.Float)
    zone          = db.Column(db.String(20))
    is_taxable    = db.Column(db.Boolean, default=True)
    is_active     = db.Column(db.Boolean, default=True, index=True)
    sort_order    = db.Column(db.Integer, default=0)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def amount_for(self, base, weight_kg=0.0):
        """Compute this surcharge against a base price, respecting the caps."""
        if self.calculation == self.PERCENT:
            raw = base * (self.value or 0.0) / 100.0
        elif self.calculation == self.PER_KG:
            raw = (self.value or 0.0) * (weight_kg or 0.0)
        else:
            raw = self.value or 0.0
        if self.min_amount:
            raw = max(raw, self.min_amount)
        if self.max_amount is not None:
            raw = min(raw, self.max_amount)
        return round(raw, 2)

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "applies_to": self.applies_to,
            "calculation": self.calculation,
            "value": self.value,
            "min_amount": self.min_amount,
            "max_amount": self.max_amount,
            "zone": self.zone,
            "is_taxable": self.is_taxable,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
        }
