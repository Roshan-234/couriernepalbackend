from .agent import agent_definitions
from .blog import blog_definitions
from .contact import contact_definitions
from .faq import faq_definitions
from .notification import notification_definitions
from .parcel import parcel_definitions
from .pricing import pricing_definitions
from .service import service_definitions
from .shipment import shipment_definitions
from .tracking import tracking_definitions
from .warehouse import warehouse_definitions

definitions = {}

def register_swagger_definitions():
    """Register all Swagger definitions."""
    definitions.update(agent_definitions)
    definitions.update(blog_definitions)
    definitions.update(contact_definitions)
    definitions.update(faq_definitions)
    definitions.update(notification_definitions)
    definitions.update(parcel_definitions)
    definitions.update(pricing_definitions)
    definitions.update(service_definitions)
    definitions.update(shipment_definitions)
    definitions.update(tracking_definitions)
    definitions.update(warehouse_definitions)
