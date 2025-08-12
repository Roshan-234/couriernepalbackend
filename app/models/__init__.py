from app.extensions import db
from .user import User
from .role import Role, UserRole

def register_models():
    """Import all models and register default roles after migrations."""
    # Import all models to register them with SQLAlchemy
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
