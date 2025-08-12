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

    # Register all Swagger definitions
    register_swagger_definitions()

    # Initialize the API with the Flask app
    api.init_app(app)
