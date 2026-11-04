import os
from datetime import timedelta
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Load .env into environment
load_dotenv()

class Config:
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

    # Used where a message needs a number to fall back to — a parcel can be
    # out for delivery before a named rider is assigned, and "Rider: ," is
    # not something to send a customer.
    COMPANY_PHONE = os.getenv("COMPANY_PHONE", "+977 1-5922458")
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    BACKEND_PROXY_SECRET = os.getenv('BACKEND_PROXY_SECRET', '')
    TRUSTED_PROXY_HOPS = int(os.getenv('TRUSTED_PROXY_HOPS', '0'))
    AUTO_CREATE_SCHEMA = os.getenv('AUTO_CREATE_SCHEMA', 'true').lower() == 'true'

    # Whether customer notifications actually leave the building.
    #
    # Off unless a deployment says otherwise, and that default is the point.
    # Every notification is recorded either way, so the office can read the
    # log and see exactly what would have gone to whom before anyone is
    # mailed. MAIL_SERVER points at the company's real SMTP server and the
    # customer list is twenty years old; wiring the lifecycle up and
    # starting to send are two separate decisions.
    NOTIFICATIONS_ENABLED = os.getenv('NOTIFICATIONS_ENABLED', 'false').lower() == 'true'
    RATELIMIT_DEFAULT = '300 per minute'

    #Mail Configuration
    MAIL_SERVER         = os.getenv("MAIL_SERVER")
    MAIL_PORT           = int(os.getenv("MAIL_PORT", 465))
    MAIL_USE_TLS        = os.getenv("MAIL_USE_TLS", "False") == "True"
    MAIL_USE_SSL        = os.getenv("MAIL_USE_SSL", "False") == "True"
    MAIL_USERNAME       = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD       = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER")

    # Use SQLite for development
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///app.db")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_STORAGE_URI = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')
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

    # Whether /auth/login and /auth/refresh also put the tokens in the JSON
    # body. API clients and the test suite read them from there; a browser
    # ignores them and uses the HttpOnly cookies. Leaving them in the body in
    # production would undo that protection, because any XSS could call
    # refresh and read the bearer token straight out of the response.
    AUTH_TOKENS_IN_BODY = os.getenv('AUTH_TOKENS_IN_BODY', 'true').lower() == 'true'

    # Secrets stored in the database (carrier and gateway credentials) are
    # encrypted with this. Unset, app/crypto.py derives one from SECRET_KEY,
    # which is fine for development but ties them to the session secret.
    FIELD_ENCRYPTION_KEY = os.getenv('FIELD_ENCRYPTION_KEY')
    FIELD_ENCRYPTION_KEYS = os.getenv('FIELD_ENCRYPTION_KEYS')

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    AUTO_CREATE_SCHEMA = False
    # Cookies must only travel over HTTPS in production.
    JWT_COOKIE_SECURE = True
    # The browser is the only production client, and it reads cookies. Keeping
    # the token out of the body is what makes HttpOnly worth having.
    AUTH_TOKENS_IN_BODY = os.getenv('AUTH_TOKENS_IN_BODY', 'false').lower() == 'true'

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
        # charset=utf8mb4 is not optional for this application. A shared
        # cPanel MySQL commonly defaults the connection to latin1 or to
        # utf8mb3, and this database stores Nepali Devanagari throughout —
        # notification templates, advert copy, site notices, customer names
        # and addresses. On a latin1 connection that arrives as mojibake and
        # the damage is done at write time, so no later fix recovers it.
        # utf8mb3 would also silently reject anything outside the basic
        # plane, which includes emoji in a customer's name or address.
        #
        # The server's own default is not enough: the *connection* charset is
        # what governs the bytes, and it is set here.
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{quote_plus(_user)}:{quote_plus(_password or '')}"
            f"@{_host}/{_database}?charset=utf8mb4"
        )

class TestingConfig(Config):
    TESTING = True
    AUTO_CREATE_SCHEMA = True
    BACKEND_PROXY_SECRET = ""
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
