from flask import Flask
from .config import config
from .extensions import db, migrate, bcrypt, cors, limiter, jwt, mail
from .models import register_models, seed_roles
from .routes.health import health_ns
from .routes.admin import bp as admin_bp
from .swagger_setup import init_swagger

def create_app(env='default'):
    app = Flask(__name__)
    app.config.from_object(config[env])

    # Match routes with and without a trailing slash instead of issuing a 308
    # redirect. Behind the frontend's same-origin proxy, Flask's trailing-slash
    # redirect resolves to the backend's own origin and bounces the browser
    # cross-origin. Must be set before any routes are registered.
    app.url_map.strict_slashes = False

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})
    limiter.init_app(app)
    jwt.init_app(app)
    mail.init_app(app)

    # Initialize Swagger / all API namespaces
    init_swagger(app)

    # JWT blocklist
    @jwt.token_in_blocklist_loader
    def check_if_revoked(jwt_header, jwt_payload):
        from app.models.user_session import UserSession
        return UserSession.query.filter_by(jti=jwt_payload.get("jti", ""), is_revoked=True).first() is not None

    # Audit middleware
    from app.middleware.audit import audit_request
    app.after_request(audit_request)

    # Register standalone blueprints
    app.register_blueprint(health_ns, url_prefix='/api')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')

    with app.app_context():
        register_models()
        db.create_all()
        seed_roles()

    return app
