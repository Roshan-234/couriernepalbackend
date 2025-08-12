from flask import Flask
from .config import config
from .extensions import db, migrate, bcrypt, cors, limiter, jwt, mail
from .models import register_models
from .routes.health import health_ns
from .routes.admin import bp as admin_bp
from .swagger_setup import init_swagger

def create_app(env='default'):
    app = Flask(__name__)
    app.config.from_object(config[env])

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)        # <— this wires up flask-migrate
    bcrypt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})
    limiter.init_app(app)
    jwt.init_app(app)
    mail.init_app(app)

    # Initialize Swagger documentation
    init_swagger(app)

    # Register blueprints/namespaces
    app.register_blueprint(health_ns, url_prefix='/api')
    app.register_blueprint(blog_bp, url_prefix='/api/blog')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')

    with app.app_context():
        register_models()

    return app
