import os
from datetime import timedelta
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Load .env into environment
load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    #Mail Configuration
    MAIL_SERVER         = os.getenv("MAIL_SERVER")
    MAIL_PORT           = int(os.getenv("MAIL_PORT", 465))
    MAIL_USE_TLS        = os.getenv("MAIL_USE_TLS", "False") == "True"
    MAIL_USE_SSL        = os.getenv("MAIL_USE_SSL", "False") == "True"
    MAIL_USERNAME       = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD       = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER")

    # Use SQLite for development
    SQLALCHEMY_DATABASE_URI = "sqlite:///app.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)

    # --- JWT cookie / session settings ---------------------------------------
    # Tokens are accepted from BOTH the Authorization header (API clients,
    # tests, and server-to-server calls from the Next.js route handlers) and
    # from cookies (the browser). The browser never sees the token in JS: it
    # is stored in an HttpOnly cookie set by the login endpoint.
    JWT_TOKEN_LOCATION = ["headers", "cookies"]
    JWT_ACCESS_COOKIE_NAME = "access_token"   # matches Next middleware + getToken()
    JWT_COOKIE_SAMESITE = "Lax"               # frontend + backend share one domain
    JWT_COOKIE_CSRF_PROTECT = True            # double-submit CSRF for cookie auth
    JWT_ACCESS_COOKIE_PATH = "/"
    JWT_REFRESH_COOKIE_PATH = "/"
    # HTTPS-only cookies are enabled per-environment (see ProductionConfig).
    JWT_COOKIE_SECURE = False

    # Required so Flask-RESTX lets Flask-JWT-Extended's error handlers run.
    # Without this, with debug=False every auth failure (missing/expired/
    # invalid token) is swallowed by RESTX and returned as a generic 500
    # instead of a proper 401/422. Real unhandled exceptions are still
    # caught and returned as 500 JSON by RESTX.
    PROPAGATE_EXCEPTIONS = True

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    # Cookies must only travel over HTTPS in production.
    JWT_COOKIE_SECURE = True

    # Shared-MySQL connection hygiene. Each Passenger worker keeps its own pool,
    # so keep pools SMALL to stay under the host's per-user connection cap, and
    # recycle/pre-ping so idle connections dropped by MySQL's wait_timeout don't
    # surface as 500s (which would hammer the app with retries).
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 2,          # steady connections per worker
        "max_overflow": 3,       # burst headroom (max 5 per worker)
        "pool_recycle": 280,     # < typical MySQL wait_timeout (300s)
        "pool_pre_ping": True,   # verify a connection before using it
        "pool_timeout": 10,
    }

    # Use MySQL in production, built from the credentials in .env.
    _host = os.getenv("MYSQL_HOST")
    _user = os.getenv("MYSQL_USER")
    _password = os.getenv("MYSQL_PASSWORD")
    _database = os.getenv("MYSQL_DATABASE")
    if _host and _user and _database:
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{quote_plus(_user)}:{quote_plus(_password or '')}"
            f"@{_host}/{_database}"
        )

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    JWT_SECRET_KEY = "test-secret"
    JWT_ACCESS_TOKEN_EXPIRES = False
    MAIL_SUPPRESS_SEND = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-secret"
    RATELIMIT_ENABLED = False          # Disable rate limiting in tests
    RATELIMIT_STORAGE_URL = "memory://"

config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig
}
