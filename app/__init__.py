from flask import Flask
from .config import config
from .extensions import db, migrate, bcrypt, cors, limiter, jwt, api, mail
from .models import register_models
from .routes.health import health_ns
from .auth.routes import auth_ns
from .users.routes import user_ns
# later: import other namespaces (users, auth, etc.)

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
    api.init_app(app)
    mail.init_app(app)
    api.add_namespace(auth_ns, path="/api/auth")
    api.add_namespace(user_ns, path="/api/users")

    # Register blueprints/namespaces
    app.register_blueprint(health_ns, url_prefix='/api')

    with app.app_context():
        register_models()

    return app
