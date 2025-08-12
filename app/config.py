import os
from datetime import timedelta
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

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig
}
