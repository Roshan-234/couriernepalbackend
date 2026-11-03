"""Zone-based quote engine for domestic and international parcels.

Pure logic: no Flask, no request parsing. ``quote()`` takes plain keyword
criteria and returns a breakdown dict a customer could read line by line.

The order of operations is fixed, because every step feeds the next:

    1. chargeable weight   max(actual, volumetric), floored at the card's
                           minimum, rounded up to the weight step
    2. rate card           merchant contract beats default, both must be
                           effective today for the scope and service level
    3. lane                the covering entry with the highest specificity
    4. freight             max(base + per_kg * chargeable, min_charge)
    5. surcharges          matching ``Surcharge`` rows, then the card's fuel
                           percent, COD fee and insurance percent
    6. tax                 VAT on freight plus the taxable surcharges

Money is rounded to 2 decimals, weights to 3.
"""
from datetime import datetime

from app.data.nepal_geo import district as geo_district, ZONE_CODES
from app.models.rate_card import RateCard, Surcharge

#: Returned as ``status`` when a quote could be priced.
OK = "ok"
#: Returned when no effective card, or no covering lane, exists.
NO_RATE = "no_rate"

DOMESTIC = RateCard.DOMESTIC
INTERNATIONAL = RateCard.INTERNATIONAL

NEPAL = "Nepal"


def _money(value):
    return round(float(value or 0.0), 2)


def _weight(value):
    return round(float(value or 0.0), 3)


def volumetric_weight(length_cm, width_cm, height_cm, divisor=5000):
    """Dimensional weight in kg, or 0.0 when the box is not fully measured.

    A missing or zero side means the sender did not give us dimensions, so
    there is no volumetric weight to compare against — returning a partial
    product here would quietly under-bill a large parcel and over-bill a
    small one.
    """
    try:
        sides = [float(length_cm or 0), float(width_cm or 0), float(height_cm or 0)]
    except (TypeError, ValueError):
        return 0.0
    if any(side <= 0 for side in sides):
        return 0.0
    divisor = float(divisor or 0) or 5000.0
    return _weight(sides[0] * sides[1] * sides[2] / divisor)


def round_up_to_step(weight_kg, step_kg):
    """Round a weight up to the next whole multiple of ``step_kg``.

    Carriers bill in bands, so 1.2 kg on a 0.5 kg step is billed as 1.5 kg.
    Worked in integer multiples of the step with a small tolerance, because
    1.5 / 0.5 lands on 2.9999999999999996 in binary floating point and would
    otherwise be rounded up to a whole extra band.
    """
    step = float(step_kg or 0)
    if step <= 0:
        return _weight(weight_kg)
    multiples = float(weight_kg) / step
    whole = int(multiples)
    if multiples - whole > 1e-9:
        whole += 1
    return _weight(whole * step)


def chargeable_weight(actual_kg, length_cm=None, width_cm=None, height_cm=None,
                      divisor=5000, minimum_kg=0.0, step_kg=0.0):
    """Billable weight plus the derivation, for the breakdown.

    Returns ``(chargeable, detail)`` where detail names which of the actual
    and volumetric weights won and what the rounding did.
    """
    actual = _weight(max(float(actual_kg or 0.0), 0.0))
    volumetric = volumetric_weight(length_cm, width_cm, height_cm, divisor)
    greater = max(actual, volumetric)
    basis = "volumetric" if volumetric > actual else "actual"

    floored = max(greater, float(minimum_kg or 0.0))
    minimum_applied = floored > greater
    final = round_up_to_step(floored, step_kg)

    return final, {
        "actual_weight_kg": actual,
        "volumetric_weight_kg": volumetric,
        "volumetric_divisor": divisor,
        "basis": basis,
        "min_chargeable_weight_kg": _weight(minimum_kg),
        "min_chargeable_applied": minimum_applied,
        "weight_step_kg": _weight(step_kg),
        "chargeable_weight_kg": final,
    }


def select_rate_card(scope=DOMESTIC, service_level="standard", merchant_id=None,
                     on_date=None):
    """The card that prices this shipment: merchant contract, else default.

    Effectiveness is checked in Python via ``is_effective`` rather than in SQL
    so that a card with open-ended dates behaves the same either way.
    """
    day = on_date or datetime.utcnow().date()
    base = RateCard.query.filter_by(scope=scope, service_level=service_level,
                                    is_active=True)

    if merchant_id:
        contracts = base.filter(RateCard.merchant_id == merchant_id)
        for card in contracts.order_by(RateCard.id.desc()).all():
            if card.is_effective(day):
                return card

    defaults = base.filter(RateCard.merchant_id.is_(None))
    for card in defaults.order_by(RateCard.is_default.desc(), RateCard.id.desc()).all():
        if card.is_effective(day):
            return card
    return None


def _entry_matches(entry, origin_zone, destination_zone, origin_district,
                   destination_district, destination_country):
    """True when every lane field the entry pins down matches the shipment.

    Null fields are wildcards, which is what makes a zone entry a fallback
    for every district pair inside it.
    """
    pairs = [
        (entry.origin_zone, origin_zone),
        (entry.destination_zone, destination_zone),
        (entry.origin_district, origin_district),
        (entry.destination_district, destination_district),
        (entry.destination_country, destination_country),
    ]
    for wanted, actual in pairs:
        if not wanted:
            continue
        if not actual or wanted.strip().lower() != str(actual).strip().lower():
            return False
    return True


def match_lane(card, weight_kg, origin_zone=None, destination_zone=None,
               origin_district=None, destination_district=None,
               destination_country=None):
    """The most specific active entry that covers this lane and weight."""
    candidates = [
        entry for entry in card.entries
        if entry.is_active is not False
        and entry.covers_weight(weight_kg)
        and _entry_matches(entry, origin_zone, destination_zone, origin_district,
                           destination_district, destination_country)
    ]
    if not candidates:
        return None
    # Tie-break on the narrower weight band, then the newer entry, so that a
    # tariff reload does not change the answer for an unchanged lane.
    candidates.sort(key=lambda e: (e.specificity, e.min_weight_kg or 0.0, e.id or 0),
                    reverse=True)
    return candidates[0]


def _surcharge_applies(surcharge, *, scope, is_cod, is_fragile, is_insured,
                       destination_zone, is_oversize, is_weekend):
    """Whether a surcharge's ``applies_to`` trigger is true for this shipment."""
    if surcharge.zone and surcharge.zone != destination_zone:
        return False
    trigger = surcharge.applies_to or "all"
    return {
        "all": True,
        "domestic": scope == DOMESTIC,
        "international": scope == INTERNATIONAL,
        "cod": bool(is_cod),
        "fragile": bool(is_fragile),
        "insurance": bool(is_insured),
        "remote": destination_zone == "remote",
        "oversize": bool(is_oversize),
        "weekend": bool(is_weekend),
        # The card's own fuel percent covers fuel; a 'fuel' row is a tariff
        # override and is applied with the rest of the percent charges.
        "fuel": True,
    }.get(trigger, False)


def quote(origin_district=None, destination_district=None,
          destination_country=None, weight_kg=0.0,
          length_cm=None, width_cm=None, height_cm=None,
          service_level="standard", scope=None, merchant_id=None,
          is_cod=False, cod_amount=0.0, is_fragile=False, is_insured=False,
          declared_value=0.0, is_oversize=False, is_weekend=False,
          on_date=None):
    """Price a parcel and return the full breakdown.

    Always returns a dict. When nothing can be priced the dict carries
    ``status == NO_RATE`` and a ``message``, rather than raising — a missing
    tariff is an ordinary answer for a public calculator, not an error.
    """
    country = (destination_country or "").strip() or None
    if scope is None:
        scope = INTERNATIONAL if country and country.lower() != NEPAL.lower() else DOMESTIC

    origin_record = geo_district(origin_district) if origin_district else None
    destination_record = geo_district(destination_district) if destination_district else None
    origin_name = origin_record["name"] if origin_record else None
    destination_name = destination_record["name"] if destination_record else None
    origin_zone = origin_record["zone"] if origin_record else None
    destination_zone = destination_record["zone"] if destination_record else None

    card = select_rate_card(scope=scope, service_level=service_level,
                            merchant_id=merchant_id, on_date=on_date)
    if not card:
        return {
            "status": NO_RATE,
            "message": "No rate card is configured for this service. Contact us for a quote.",
            "scope": scope,
            "service_level": service_level,
        }

    charge_kg, weight_detail = chargeable_weight(
        weight_kg, length_cm, width_cm, height_cm,
        divisor=card.volumetric_divisor or 5000,
        minimum_kg=card.min_chargeable_weight_kg or 0.0,
        step_kg=card.weight_step_kg or 0.0,
    )

    entry = match_lane(card, charge_kg, origin_zone=origin_zone,
                       destination_zone=destination_zone,
                       origin_district=origin_name,
                       destination_district=destination_name,
                       destination_country=country)
    if not entry:
        return {
            "status": NO_RATE,
            "message": "No rate is configured for this route and weight. Contact us for a quote.",
            "scope": scope,
            "service_level": service_level,
            "rate_card": {"id": card.id, "code": card.code, "name": card.name},
            "currency": card.currency,
            "weight": weight_detail,
        }

    freight = _money(max(
        (entry.base_price or 0.0) + (entry.price_per_kg or 0.0) * charge_kg,
        entry.min_charge or 0.0,
    ))

    lines = []

    configured = Surcharge.query.filter_by(is_active=True).order_by(
        Surcharge.sort_order.asc(), Surcharge.id.asc()).all()
    for surcharge in configured:
        if not _surcharge_applies(surcharge, scope=scope, is_cod=is_cod,
                                  is_fragile=is_fragile, is_insured=is_insured,
                                  destination_zone=destination_zone,
                                  is_oversize=is_oversize, is_weekend=is_weekend):
            continue
        amount = surcharge.amount_for(freight, charge_kg)
        if not amount:
            continue
        lines.append({
            "code": surcharge.code,
            "name": surcharge.name,
            "calculation": surcharge.calculation,
            "amount": _money(amount),
            "is_taxable": bool(surcharge.is_taxable),
        })

    # Tariff-wide charges live on the card rather than as Surcharge rows.
    if card.fuel_surcharge_percent:
        lines.append({
            "code": "FUEL",
            "name": "Fuel surcharge",
            "calculation": Surcharge.PERCENT,
            "amount": _money(freight * card.fuel_surcharge_percent / 100.0),
            "is_taxable": True,
        })

    cod_value = _money(cod_amount) if is_cod else 0.0
    if is_cod and (card.cod_fee_percent or card.cod_fee_min):
        fee = max(cod_value * (card.cod_fee_percent or 0.0) / 100.0,
                  card.cod_fee_min or 0.0)
        if fee:
            lines.append({
                "code": "COD_FEE",
                "name": "Cash on delivery fee",
                "calculation": Surcharge.PERCENT,
                "amount": _money(fee),
                "is_taxable": True,
            })

    declared = _money(declared_value)
    if is_insured and card.insurance_percent and declared > 0:
        lines.append({
            "code": "INSURANCE",
            "name": "Insurance",
            "calculation": Surcharge.PERCENT,
            "amount": _money(declared * card.insurance_percent / 100.0),
            "is_taxable": True,
        })

    surcharge_total = _money(sum(line["amount"] for line in lines))
    taxable_base = _money(freight + sum(line["amount"] for line in lines if line["is_taxable"]))
    tax = _money(taxable_base * (card.vat_percent or 0.0) / 100.0)
    total = _money(freight + surcharge_total + tax)

    return {
        "status": OK,
        "currency": card.currency,
        "scope": scope,
        "service_level": service_level,
        "rate_card": {
            "id": card.id,
            "code": card.code,
            "name": card.name,
            "is_contract": card.merchant_id is not None,
            "merchant_id": card.merchant_id,
        },
        "weight": weight_detail,
        "chargeable_weight_kg": charge_kg,
        "lane": {
            "entry_id": entry.id,
            "origin_district": origin_name,
            "destination_district": destination_name,
            "origin_zone": entry.origin_zone or origin_zone,
            "destination_zone": entry.destination_zone or destination_zone,
            "destination_country": entry.destination_country or country,
            "specificity": entry.specificity,
            "min_weight_kg": entry.min_weight_kg,
            "max_weight_kg": entry.max_weight_kg,
        },
        "freight": freight,
        "surcharges": lines,
        "surcharge_total": surcharge_total,
        "taxable_amount": taxable_base,
        "vat_percent": card.vat_percent,
        "tax": tax,
        "total": total,
        "cod_amount": cod_value,
        "declared_value": declared,
        "transit_days": {
            "min": entry.transit_days_min,
            "max": entry.transit_days_max,
        },
    }


def known_zone(code):
    """True when ``code`` is one of the delivery zone codes."""
    return code in ZONE_CODES


def known_district(name):
    """True when ``name`` resolves to one of the 77 districts."""
    return geo_district(name) is not None
