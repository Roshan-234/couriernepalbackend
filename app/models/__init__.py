from app.extensions import db
from .user import User
from .role import Role, UserRole

def register_models():
    """Create all tables and seed default roles."""
    db.create_all()

    # seed default roles if they don't exist
    for name, desc in [
        ("super_admin", "Full access"),
        ("admin",       "User & shipment management"),
        ("manager",     "Operational access"),
        ("staff",       "Limited internal access"),
        ("customer",    "End-user")
    ]:
        if not Role.query.filter_by(name=name).first():
            db.session.add(Role(name=name, description=desc))
    db.session.commit()
