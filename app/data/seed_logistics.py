"""Starter data for the logistics modules.

Run with ``flask seed-logistics``. Every step is idempotent and keyed on a
natural code, so it is safe to re-run after a deploy: existing rows are left
alone rather than overwritten, because operations will have edited them.

Nothing here is enabled by default. Carrier connectors and payment gateways
are seeded inactive and without credentials — somebody has to enter those and
switch each one on deliberately. The rate card is real and priced so the
quote engine has something to answer with on day one; treat the numbers as a
starting tariff for the business to revise, not as gospel.
"""

from datetime import date

from app.extensions import db
from app.data.nepal_geo import (
    DISTRICTS,
    ZONE_CITIES,
    ZONE_HILLS,
    ZONE_REMOTE,
    ZONE_VALLEY,
)


# ── Branch network ──────────────────────────────────────────────────────────

#: code, name, type, district, city, address, phone, handles_international
BRANCHES = [
    ("KTM-HQ", "Kathmandu Head Office", "hub", "Kathmandu", "Kathmandu",
     "Nayabazar, Kathmandu", "+977-1-5922458", True),
    ("KTM-TIA", "Tribhuvan Airport Cargo", "hub", "Kathmandu", "Kathmandu",
     "TIA Cargo Complex, Sinamangal", "+977-1-5922458", True),
    ("LTP-01", "Lalitpur Branch", "branch", "Lalitpur", "Lalitpur",
     "Pulchowk, Lalitpur", "+977-1-5922458", False),
    ("BKT-01", "Bhaktapur Branch", "branch", "Bhaktapur", "Bhaktapur",
     "Suryabinayak, Bhaktapur", "+977-1-5922458", False),
    ("PKR-01", "Pokhara Branch", "hub", "Kaski", "Pokhara",
     "Lakeside, Pokhara", "+977-61-000000", False),
    ("BRT-01", "Biratnagar Branch", "hub", "Morang", "Biratnagar",
     "Main Road, Biratnagar", "+977-21-000000", False),
    ("BRG-01", "Birgunj Branch", "hub", "Parsa", "Birgunj",
     "Ghantaghar, Birgunj", "+977-51-000000", True),
    ("BTL-01", "Butwal Branch", "branch", "Rupandehi", "Butwal",
     "Traffic Chowk, Butwal", "+977-71-000000", False),
    ("BRT-CTW", "Chitwan Branch", "branch", "Chitwan", "Bharatpur",
     "Narayangarh, Bharatpur", "+977-56-000000", False),
    ("NPG-01", "Nepalgunj Branch", "hub", "Banke", "Nepalgunj",
     "Birendra Chowk, Nepalgunj", "+977-81-000000", False),
    ("DHG-01", "Dhangadhi Branch", "branch", "Kailali", "Dhangadhi",
     "Main Road, Dhangadhi", "+977-91-000000", False),
    ("ITH-01", "Itahari Branch", "branch", "Sunsari", "Itahari",
     "Dharan Road, Itahari", "+977-25-000000", False),
    ("JNK-01", "Janakpur Branch", "branch", "Dhanusha", "Janakpur",
     "Station Road, Janakpur", "+977-41-000000", False),
    ("SKT-01", "Surkhet Branch", "branch", "Surkhet", "Birendranagar",
     "Birendranagar, Surkhet", "+977-83-000000", False),
]


def seed_branches():
    from app.models.branch import Branch

    created = 0
    for code, name, kind, district, city, address, phone, intl in BRANCHES:
        if Branch.query.filter_by(code=code).first():
            continue
        record = next((d for d in DISTRICTS if d["name"] == district), None)
        db.session.add(Branch(
            code=code,
            name=name,
            type=kind,
            district=district,
            province=record["province"] if record else None,
            city=city,
            address=address,
            phone=phone,
            email="moonlightfreight@gmail.com",
            handles_cod=True,
            handles_international=intl,
            serves_districts=[district],
            is_active=True,
        ))
        created += 1
    db.session.commit()
    return created


# ── Domestic rate card ──────────────────────────────────────────────────────

#: Starting tariff in NPR. (origin zone, destination zone) -> (base, per kg).
#: Priced from the Kathmandu Valley outward; the reverse lane costs the same.
LANE_PRICES = {
    (ZONE_VALLEY, ZONE_VALLEY): (80.0, 20.0),
    (ZONE_VALLEY, ZONE_CITIES): (130.0, 45.0),
    (ZONE_VALLEY, ZONE_HILLS): (165.0, 65.0),
    (ZONE_VALLEY, ZONE_REMOTE): (280.0, 130.0),
    (ZONE_CITIES, ZONE_CITIES): (120.0, 40.0),
    (ZONE_CITIES, ZONE_HILLS): (160.0, 60.0),
    (ZONE_CITIES, ZONE_REMOTE): (290.0, 135.0),
    (ZONE_HILLS, ZONE_HILLS): (175.0, 70.0),
    (ZONE_HILLS, ZONE_REMOTE): (310.0, 145.0),
    (ZONE_REMOTE, ZONE_REMOTE): (350.0, 160.0),
}

#: (min kg, max kg) bands. The open-ended top band carries the per-kg rate.
WEIGHT_BANDS = [(0.0, 1.0), (1.0, 5.0), (5.0, 20.0), (20.0, None)]

#: Express costs more and moves faster than the standard service.
SERVICE_LEVELS = {
    "standard": (1.0, (2, 5)),
    "express": (1.6, (1, 3)),
}


def _transit_for(origin_zone, destination_zone, base_days):
    """Remote lanes add days on top of the service level's base."""
    extra = 0
    for zone in (origin_zone, destination_zone):
        if zone == ZONE_REMOTE:
            extra += 3
        elif zone == ZONE_HILLS:
            extra += 1
    low, high = base_days
    return low + extra, high + extra


def seed_rate_cards():
    from app.models.rate_card import RateCard, RateCardEntry

    created = 0
    for level, (multiplier, base_days) in SERVICE_LEVELS.items():
        code = f"DOM-{level.upper()}"
        if RateCard.query.filter_by(code=code).first():
            continue
        card = RateCard(
            code=code,
            name=f"Domestic {level.title()} (Nepal)",
            scope=RateCard.DOMESTIC,
            service_level=level,
            currency="NPR",
            is_default=(level == "standard"),
            is_active=True,
            effective_from=date.today(),
            volumetric_divisor=5000,
            min_chargeable_weight_kg=0.5,
            weight_step_kg=0.5,
            fuel_surcharge_percent=0.0,
            vat_percent=13.0,
            cod_fee_percent=1.5,
            cod_fee_min=25.0,
            insurance_percent=1.0,
            notes="Seeded starting tariff. Review before going live.",
        )
        db.session.add(card)
        db.session.flush()

        for (origin_zone, destination_zone), (base, per_kg) in LANE_PRICES.items():
            pairs = {(origin_zone, destination_zone), (destination_zone, origin_zone)}
            for o_zone, d_zone in pairs:
                low, high = _transit_for(o_zone, d_zone, base_days)
                for min_kg, max_kg in WEIGHT_BANDS:
                    db.session.add(RateCardEntry(
                        rate_card_id=card.id,
                        origin_zone=o_zone,
                        destination_zone=d_zone,
                        min_weight_kg=min_kg,
                        max_weight_kg=max_kg,
                        base_price=round(base * multiplier, 2),
                        price_per_kg=round(per_kg * multiplier, 2),
                        min_charge=round(base * multiplier, 2),
                        transit_days_min=low,
                        transit_days_max=high,
                        is_active=True,
                    ))
                    created += 1
    db.session.commit()
    return created


# ── Surcharges ──────────────────────────────────────────────────────────────

#: code, name, applies_to, calculation, value, min, max, taxable
SURCHARGES = [
    ("REMOTE", "Remote area delivery", "remote", "flat", 150.0, 0.0, None, True),
    ("FRAGILE", "Fragile handling", "fragile", "percent", 5.0, 50.0, 1000.0, True),
    ("OVERSIZE", "Oversize parcel", "oversize", "flat", 200.0, 0.0, None, True),
    ("WEEKEND", "Weekend or holiday delivery", "weekend", "flat", 100.0, 0.0, None, True),
    ("INTL_DOC", "International documentation", "international", "flat", 500.0, 0.0, None, True),
]


def seed_surcharges():
    from app.models.rate_card import Surcharge

    created = 0
    for i, (code, name, applies, calc, value, lo, hi, taxable) in enumerate(SURCHARGES):
        if Surcharge.query.filter_by(code=code).first():
            continue
        db.session.add(Surcharge(
            code=code, name=name, applies_to=applies, calculation=calc,
            value=value, min_amount=lo, max_amount=hi,
            is_taxable=taxable, is_active=True, sort_order=i,
        ))
        created += 1
    db.session.commit()
    return created


# ── Carrier connectors ──────────────────────────────────────────────────────

#: Seeded inactive and without credentials. The tracking URL is the public
#: consumer page, which works today; the API fields are left for whoever
#: signs the carrier agreement to fill in.
CARRIERS = [
    ("dhl", "DHL Express", "https://www.dhl.com/us-en/home/tracking.html?tracking-id={tracking_no}",
     "https://api-eu.dhl.com", "api_key", "DHL-API-Key", ""),
    ("fedex", "FedEx", "https://www.fedex.com/fedextrack/?trknbr={tracking_no}",
     "https://apis.fedex.com", "oauth2", "Authorization", "Bearer"),
    ("ups", "UPS", "https://www.ups.com/track?loc=en_US&tracknum={tracking_no}",
     "https://onlinetools.ups.com", "oauth2", "Authorization", "Bearer"),
    ("aramex", "Aramex", "https://www.aramex.com/us/en/track/results?ShipmentNumber={tracking_no}",
     "https://ws.aramex.net", "basic", "Authorization", "Basic"),
    ("tnt", "TNT", "https://www.tnt.com/express/en_gc/site/tracking.html?searchType=CON&cons={tracking_no}",
     "", "api_key", "Authorization", "Bearer"),
    ("dpd", "DPD", "https://tracking.dpd.de/status/en_US/parcel/{tracking_no}",
     "", "api_key", "Authorization", "Bearer"),
    ("ems", "EMS", "https://www.ems.post/en/global-network/tracking?tracking-number={tracking_no}",
     "", "none", "", ""),
    ("nepal-post", "Nepal Post", "https://www.nepalpost.gov.np/tracking/?trackingNumber={tracking_no}",
     "", "none", "", ""),
    ("skynet", "Skynet", "https://www.skynetworldwide.net/track.aspx?trackingno={tracking_no}",
     "", "api_key", "Authorization", "Bearer"),
]

#: A sensible default translation from carrier vocabulary to our statuses.
DEFAULT_STATUS_MAP = {
    "PU": "picked_up",
    "PICKED_UP": "picked_up",
    "IT": "in_transit",
    "IN_TRANSIT": "in_transit",
    "TRANSIT": "in_transit",
    "OFD": "out_for_delivery",
    "OUT_FOR_DELIVERY": "out_for_delivery",
    "OK": "delivered",
    "DELIVERED": "delivered",
    "DL": "delivered",
    "EXCEPTION": "on_hold",
    "CUSTOMS": "customs_hold",
    "RETURNED": "returned",
    "RT": "returned",
}


def seed_carriers():
    from app.models.carrier import CarrierConnector

    created = 0
    for i, (code, name, url_tpl, base, auth, header, prefix) in enumerate(CARRIERS):
        if CarrierConnector.query.filter_by(code=code).first():
            continue
        db.session.add(CarrierConnector(
            code=code,
            name=name,
            display_name=name,
            tracking_url_template=url_tpl,
            api_base_url=base or None,
            auth_type=auth,
            auth_header_name=header or None,
            auth_header_prefix=prefix or None,
            content_type="application/json",
            supports_tracking=True,
            supports_booking=False,
            is_active=False,
            is_sandbox=True,
            status_map=dict(DEFAULT_STATUS_MAP),
            # Paths a typical carrier tracking response uses; adjust per carrier.
            events_path="shipments.0.events",
            event_status_path="statusCode",
            event_time_path="timestamp",
            event_location_path="location.address.addressLocality",
            event_description_path="description",
            timeout_seconds=20,
            max_retries=3,
            sort_order=i,
            supported_countries=[],
        ))
        created += 1
    db.session.commit()
    return created


# ── Payment gateways ────────────────────────────────────────────────────────

#: Seeded inactive and with no credentials. Base URLs are the published
#: sandbox endpoints where they are public; the rest is left blank.
GATEWAYS = [
    ("esewa", "eSewa", "https://rc-epay.esewa.com.np", "https://epay.esewa.com.np", 10.0),
    ("khalti", "Khalti", "https://a.khalti.com", "https://khalti.com", 10.0),
    ("fonepay", "Fonepay", "", "", 10.0),
    ("connectips", "ConnectIPS", "", "", 10.0),
    ("imepay", "IME Pay", "", "", 10.0),
    ("bank_transfer", "Bank transfer", "", "", 0.0),
    ("cash", "Cash at branch", "", "", 0.0),
]


def seed_gateways():
    from app.models.payment import PaymentGateway

    created = 0
    for i, (code, name, sandbox, live, minimum) in enumerate(GATEWAYS):
        if PaymentGateway.query.filter_by(code=code).first():
            continue
        db.session.add(PaymentGateway(
            code=code,
            name=name,
            display_name=name,
            sandbox_base_url=sandbox or None,
            api_base_url=live or None,
            is_active=False,
            is_sandbox=True,
            supported_currencies=["NPR"],
            min_amount=minimum,
            sort_order=i,
            instructions=f"Enter the {name} merchant credentials, then activate.",
        ))
        created += 1
    db.session.commit()
    return created


# ── Notification templates ──────────────────────────────────────────────────

#: code, event, channel, subject, English body, Nepali body
TEMPLATES = [
    ("sms_shipment_created", "shipment.created", "sms", None,
     "Moonlight Freight: your parcel {tracking_no} is booked. Track it at {tracking_url}",
     "मूनलाइट फ्रेट: तपाईंको पार्सल {tracking_no} बुक भयो। {tracking_url} मा हेर्नुहोस्।"),
    ("sms_picked_up", "shipment.picked_up", "sms", None,
     "Your parcel {tracking_no} has been picked up and is on its way.",
     "तपाईंको पार्सल {tracking_no} संकलन भयो र बाटोमा छ।"),
    ("sms_out_for_delivery", "shipment.out_for_delivery", "sms", None,
     "Your parcel {tracking_no} is out for delivery today. Rider: {rider_name}, {rider_phone}",
     "तपाईंको पार्सल {tracking_no} आज डेलिभरीमा छ। राइडर: {rider_name}, {rider_phone}"),
    ("sms_delivered", "shipment.delivered", "sms", None,
     "Your parcel {tracking_no} was delivered to {recipient_name}. Thank you.",
     "तपाईंको पार्सल {tracking_no} {recipient_name} लाई बुझाइयो। धन्यवाद।"),
    ("sms_delivery_failed", "shipment.delivery_failed", "sms", None,
     "We could not deliver {tracking_no} ({reason}). We will try again on {next_date}.",
     "हामीले {tracking_no} पुर्‍याउन सकेनौं ({reason})। {next_date} मा पुन: प्रयास गर्नेछौं।"),
    ("sms_delivery_otp", "delivery.otp", "sms", None,
     "Your Moonlight Freight delivery code for {tracking_no} is {otp}. Share it only with the rider.",
     "{tracking_no} को डेलिभरी कोड {otp} हो। राइडरलाई मात्र दिनुहोस्।"),
    ("email_shipment_created", "shipment.created", "email",
     "Your shipment {tracking_no} is booked",
     "Hello {customer_name},\n\nYour shipment {tracking_no} from {origin} to {destination} is booked.\n"
     "Estimated delivery: {estimated_delivery}.\nTrack it any time at {tracking_url}.\n\n"
     "Moonlight Freight Pvt Ltd",
     "नमस्ते {customer_name},\n\nतपाईंको {origin} बाट {destination} सम्मको शिपमेन्ट {tracking_no} बुक भयो।\n"
     "अनुमानित डेलिभरी: {estimated_delivery}।\n{tracking_url} मा ट्र्याक गर्नुहोस्।\n\n"
     "मूनलाइट फ्रेट प्रा. लि."),
    ("email_delivered", "shipment.delivered", "email",
     "Your shipment {tracking_no} was delivered",
     "Hello {customer_name},\n\nYour shipment {tracking_no} was delivered to {recipient_name} "
     "on {delivered_at}.\n\nThank you for shipping with us.\n\nMoonlight Freight Pvt Ltd",
     "नमस्ते {customer_name},\n\nतपाईंको शिपमेन्ट {tracking_no} {delivered_at} मा "
     "{recipient_name} लाई बुझाइयो।\n\nधन्यवाद।\n\nमूनलाइट फ्रेट प्रा. लि."),
    ("sms_cod_remitted", "cod.remitted", "sms", None,
     "COD remittance {remittance_no} of NPR {net_payable} has been paid to your account.",
     "COD भुक्तानी {remittance_no} रु {net_payable} तपाईंको खातामा पठाइयो।"),
    ("sms_pickup_scheduled", "pickup.scheduled", "sms", None,
     "Pickup {pickup_no} is scheduled for {scheduled_date} ({time_slot}).",
     "पिकअप {pickup_no} {scheduled_date} ({time_slot}) मा तय भएको छ।"),
]

#: Placeholders each event makes available, shown to whoever edits a template.
TEMPLATE_VARIABLES = {
    "shipment.created": ["tracking_no", "customer_name", "origin", "destination",
                         "estimated_delivery", "tracking_url"],
    "shipment.picked_up": ["tracking_no", "customer_name"],
    "shipment.out_for_delivery": ["tracking_no", "rider_name", "rider_phone"],
    "shipment.delivered": ["tracking_no", "customer_name", "recipient_name", "delivered_at"],
    "shipment.delivery_failed": ["tracking_no", "reason", "next_date"],
    "delivery.otp": ["tracking_no", "otp"],
    "cod.remitted": ["remittance_no", "net_payable"],
    "pickup.scheduled": ["pickup_no", "scheduled_date", "time_slot"],
}


def seed_templates():
    from app.models.notification_template import NotificationTemplate

    created = 0
    for code, event, channel, subject, body_en, body_ne in TEMPLATES:
        if NotificationTemplate.query.filter_by(code=code).first():
            continue
        db.session.add(NotificationTemplate(
            code=code,
            name=code.replace("_", " ").title(),
            event=event,
            channel=channel,
            subject=subject,
            body_en=body_en,
            body_ne=body_ne,
            variables=TEMPLATE_VARIABLES.get(event, []),
            is_active=True,
            send_to_sender=True,
            send_to_receiver=event in ("shipment.out_for_delivery", "delivery.otp"),
        ))
        created += 1
    db.session.commit()
    return created


def seed_sms_providers():
    from app.models.notification_template import SmsProvider

    if SmsProvider.query.filter_by(code="sparrow").first():
        return 0
    db.session.add(SmsProvider(
        code="sparrow",
        name="Sparrow SMS",
        api_base_url="https://api.sparrowsms.com",
        send_endpoint="/v2/sms/",
        http_method="POST",
        content_type="application/x-www-form-urlencoded",
        is_active=False,
        is_sandbox=True,
        is_primary=True,
        unicode_supported=True,
        max_length=160,
        param_map={"token": "token", "from": "from", "to": "to", "text": "text"},
    ))
    db.session.commit()
    return 1


# ── Entry point ─────────────────────────────────────────────────────────────

def seed_all(verbose=True):
    """Run every seeder. Safe to call repeatedly."""
    steps = [
        ("branches", seed_branches),
        ("rate card entries", seed_rate_cards),
        ("surcharges", seed_surcharges),
        ("carrier connectors", seed_carriers),
        ("payment gateways", seed_gateways),
        ("notification templates", seed_templates),
        ("sms providers", seed_sms_providers),
    ]
    totals = {}
    for label, step in steps:
        count = step()
        totals[label] = count
        if verbose:
            print(f"  {label:24} {count:4} created")
    return totals
