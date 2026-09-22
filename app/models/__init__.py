from app.extensions import db
from .user import User
from .role import Role, UserRole

def register_models():
    """Import all models to register them with SQLAlchemy."""
    from .agent import AgentProfile
    from .blog import BlogPost, BlogCategory, BlogTag, BlogComment
    from .contact import ContactForm
    from .faq import FAQ
    from .notification import NotificationLog
    from .parcel import Parcel
    from .pricing import PricingRule
    from .service import Service
    from .shipment import Shipment
    from .tracking_event import TrackingEvent
    from .warehouse import Warehouse
    from .tracking_provider import TrackingProvider
    from .audit_log import AuditLog
    from .user_session import UserSession
    from .packing_list import PackingList
    from .invoice import Invoice

def seed_roles():
    """Ensure default roles, services, and FAQs exist in the database."""
    defaults = [
        ("super_admin", "Full system access"),
        ("customer",    "Regular customer"),
        ("agent",       "Delivery agent"),
    ]
    for name, desc in defaults:
        if not Role.query.filter_by(name=name).first():
            db.session.add(Role(name=name, description=desc))
    db.session.commit()
    _seed_faqs()
    _seed_services()
    _seed_pricing_rules()
    _seed_tracking_providers()

def _seed_faqs():
    from .faq import FAQ
    defaults = [
        ("How is the shipping cost calculated?",
         "Cost is based on package weight, dimensions, origin, destination, and service type. Use our calculator for an instant estimate.",
         "pricing", 1),
        ("Do you offer discounts for bulk shipments?",
         "Yes, we provide special rates for businesses and bulk shipments. Contact us at info@couriernepal.com for details.",
         "pricing", 2),
        ("What payment methods do you accept?",
         "We accept cash, credit/debit cards, bank transfers, eSewa, Khalti, and mobile payments.",
         "payment", 3),
        ("Can I change my delivery address after shipment?",
         "Address changes are possible before the package is dispatched. Contact us immediately at +977-1-5922458.",
         "delivery", 4),
        ("How do I track my shipment?",
         "Visit our Track page and enter your tracking number to get real-time updates.",
         "tracking", 5),
        ("What areas do you cover?",
         "We cover all 77 districts of Nepal and offer international shipping to major countries worldwide.",
         "general", 6),
        ("Is my package insured?",
         "We offer optional insurance based on the declared value of your shipment. Ask our team for details.",
         "general", 7),
    ]
    for question, answer, category, order in defaults:
        if not FAQ.query.filter_by(question=question).first():
            db.session.add(FAQ(question=question, answer=answer, category=category, order=order, is_active=True))
    db.session.commit()


def _seed_services():
    from .service import Service
    defaults = [
        ("INTL_EXPRESS",      "International Express",       "3-5 business days",   "Rs. 2,000+"),
        ("INTL_STANDARD",     "International Standard",      "7-10 business days",  "Rs. 1,500+"),
        ("INTL_ECONOMY",      "International Economy",       "14-21 business days", "Rs. 1,000+"),
        ("AIR_CARGO",         "Air Cargo",                   "5-7 business days",   "Rs. 500+/kg"),
        ("SEA_CARGO",         "Sea Cargo",                   "25-45 business days", "Rs. 100+/kg"),
        ("DOCS_EXPRESS",      "Document Express",            "2-4 business days",   "Rs. 800+"),
        ("ECOMMERCE_INTL",    "E-Commerce International",    "7-14 business days",  "Custom"),
        ("DANGEROUS_GOODS",   "Dangerous Goods",             "7-10 business days",  "Custom"),
        ("PERISHABLES",       "Perishables",                 "2-3 business days",   "Custom"),
    ]
    for code, name, delivery_time, price_range in defaults:
        if not Service.query.filter_by(code=code).first():
            db.session.add(Service(
                code=code,
                name=name,
                delivery_time=delivery_time,
                price_range=price_range,
                is_active=True,
            ))
    db.session.commit()


def _seed_pricing_rules():
    from .pricing import PricingRule
    # International-only rules (origin: Nepal, destination: various)
    rules = [
        # service_type, shipment_type, origin, destination, base_price, per_kg, min_w, max_w, insurance%, tax%
        ("international", "standard",  "Nepal", "India",      700.0,  300.0, 0.5, None, 2.0, 0.0),
        ("international", "standard",  "Nepal", "China",      800.0,  350.0, 0.5, None, 2.0, 0.0),
        ("international", "standard",  "Nepal", "USA",       2000.0,  700.0, 0.5, None, 2.0, 0.0),
        ("international", "standard",  "Nepal", "UK",        2200.0,  750.0, 0.5, None, 2.0, 0.0),
        ("international", "standard",  "Nepal", "Australia", 2500.0,  800.0, 0.5, None, 2.0, 0.0),
        ("international", "standard",  "Nepal", "Global",    3000.0,  900.0, 0.5, None, 2.0, 0.0),
        ("international", "express",   "Nepal", "India",     1200.0,  500.0, 0.5, None, 2.0, 0.0),
        ("international", "express",   "Nepal", "China",     1400.0,  550.0, 0.5, None, 2.0, 0.0),
        ("international", "express",   "Nepal", "USA",       3500.0, 1000.0, 0.5, None, 2.0, 0.0),
        ("international", "express",   "Nepal", "UK",        3800.0, 1100.0, 0.5, None, 2.0, 0.0),
        ("international", "express",   "Nepal", "Australia", 4000.0, 1200.0, 0.5, None, 2.0, 0.0),
        ("international", "express",   "Nepal", "Global",    4500.0, 1300.0, 0.5, None, 2.0, 0.0),
    ]
    for (svc, stype, orig, dest, base, per_kg, min_w, max_w, ins, tax) in rules:
        exists = PricingRule.query.filter_by(
            service_type=svc, shipment_type=stype,
            origin_country=orig, destination_country=dest
        ).first()
        if not exists:
            db.session.add(PricingRule(
                service_type=svc,
                shipment_type=stype,
                origin_country=orig,
                destination_country=dest,
                base_price=base,
                price_per_kg=per_kg,
                min_weight=min_w,
                max_weight=max_w,
                insurance_percentage=ins,
                tax_percentage=tax,
                is_active=True,
            ))
    db.session.commit()


def _seed_tracking_providers():
    from .tracking_provider import TrackingProvider
    providers = [
        ("DHL",         "dhl",         "https://www.dhl.com/us-en/home/tracking.html?tracking-id={tracking_no}"),
        ("FedEx",       "fedex",       "https://www.fedex.com/fedextrack/?trknbr={tracking_no}"),
        ("Aramex",      "aramex",      "https://www.aramex.com/us/en/track/results?ShipmentNumber={tracking_no}"),
        ("TNT",         "tnt",         "https://www.tnt.com/express/en_gc/site/tracking.html?searchType=CON&cons={tracking_no}"),
        ("UPS",         "ups",         "https://www.ups.com/track?loc=en_US&tracknum={tracking_no}"),
        ("EMS",         "ems",         "https://www.ems.post/en/global-network/tracking?tracking-number={tracking_no}"),
        ("Nepal Post",  "nepal-post",  "https://www.nepalpost.gov.np/tracking/?trackingNumber={tracking_no}"),
        ("Skynet",      "skynet",      "https://www.skynetworldwide.net/track.aspx?trackingno={tracking_no}"),
        ("DPD",         "dpd",         "https://tracking.dpd.de/status/en_US/parcel/{tracking_no}"),
    ]
    for name, code, api_url_template in providers:
        if not TrackingProvider.query.filter_by(code=code).first():
            db.session.add(TrackingProvider(
                name=name,
                code=code,
                api_url_template=api_url_template,
                is_active=False,
                supported_countries="all",
            ))
    db.session.commit()
