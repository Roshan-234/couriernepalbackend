"""
This module configures the Swagger API documentation.
"""
from flask_restx import Api
from app.extensions import api
from app.swagger import register_swagger_definitions

# Import all the namespaces
from app.auth.routes import auth_ns
from app.blog.routes import blog_ns
from app.shipments.routes import ship_ns
from app.users.routes import user_ns
from app.tracking.routes import tracking_ns
from app.pricing.routes import pricing_ns
from app.services.routes import service_ns
from app.warehouses.routes import warehouse_ns
from app.faq.routes import faq_ns
from app.contact.routes import contact_ns
from app.agents.routes import agent_ns
from app.tracking_providers.routes import tp_ns
from app.audit.routes import audit_ns
from app.sessions.routes import sessions_ns
from app.packing_lists.routes import pl_ns
from app.invoices.routes import inv_ns
# Logistics operations
from app.branches.routes import branch_ns
from app.riders.routes import rider_ns
from app.pickups.routes import pickup_ns
from app.deliveries.routes import delivery_ns
from app.cod.routes import cod_ns
from app.payments.routes import payment_ns
from app.rates.routes import rate_ns
from app.carriers.routes import carrier_ns
from app.merchant.routes import merchant_ns
from app.webhooks.routes import webhook_ns
from app.notifications.routes import notification_ns
from app.claims.routes import claim_ns
from app.geo.routes import geo_ns
# Privacy, analytics, advertising and marketing
from app.consent.routes import consent_ns
from app.analytics.routes import analytics_ns
from app.marketing.routes import marketing_ns
from app.privacy_requests.routes import privacy_ns
from app.advertising.routes import ads_ns
from app.reviews.routes import review_ns

def init_swagger(app):
    """Initialize Swagger documentation."""
    # Register all API namespaces
    api.add_namespace(auth_ns, path='/api/auth')
    api.add_namespace(blog_ns, path='/api/blog')
    api.add_namespace(ship_ns, path='/api/shipments')
    api.add_namespace(user_ns, path='/api/users')
    api.add_namespace(tracking_ns, path='/api/tracking')
    api.add_namespace(pricing_ns, path='/api/pricing')
    api.add_namespace(service_ns, path='/api/services')
    api.add_namespace(warehouse_ns, path='/api/warehouses')
    api.add_namespace(faq_ns, path='/api/faq')
    api.add_namespace(contact_ns, path='/api/contact')
    api.add_namespace(agent_ns, path='/api/agents')
    api.add_namespace(tp_ns, path='/api/tracking-providers')
    api.add_namespace(audit_ns, path='/api/audit')
    api.add_namespace(sessions_ns, path='/api/sessions')
    api.add_namespace(pl_ns, path='/api/packing-lists')
    api.add_namespace(inv_ns, path='/api/invoices')
    api.add_namespace(branch_ns, path='/api/branches')
    api.add_namespace(rider_ns, path='/api/riders')
    api.add_namespace(pickup_ns, path='/api/pickups')
    api.add_namespace(delivery_ns, path='/api/deliveries')
    api.add_namespace(cod_ns, path='/api/cod')
    api.add_namespace(payment_ns, path='/api/payments')
    api.add_namespace(rate_ns, path='/api/rates')
    api.add_namespace(carrier_ns, path='/api/carriers')
    api.add_namespace(merchant_ns, path='/api/merchant')
    api.add_namespace(webhook_ns, path='/api/webhooks')
    api.add_namespace(notification_ns, path='/api/notifications')
    api.add_namespace(claim_ns, path='/api/claims')
    api.add_namespace(geo_ns, path='/api/geo')
    api.add_namespace(consent_ns, path='/api/consent')
    api.add_namespace(analytics_ns, path='/api/analytics')
    api.add_namespace(marketing_ns, path='/api/marketing')
    api.add_namespace(privacy_ns, path='/api/privacy')
    api.add_namespace(ads_ns, path='/api/ads')
    api.add_namespace(review_ns, path='/api/reviews')

    # Register all Swagger definitions
    register_swagger_definitions()

    # Initialize the API with the Flask app
    api.init_app(app)
