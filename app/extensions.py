from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_limiter import Limiter
from app.net import rate_limit_key
from flask_jwt_extended import JWTManager
from flask_restx import Api
from flask_mail import Mail

db      = SQLAlchemy()
migrate = Migrate()
bcrypt  = Bcrypt()
mail = Mail()
# Origins are set properly in create_app from FRONTEND_URL; this instance is
# only a placeholder, so it must not carry a permissive default that would
# become live if that init_app call were ever removed.
cors = CORS()
# Keyed per account where there is one, because behind the frontend's /api
# proxy every request shares a single source address. See app/net.py.
limiter = Limiter(key_func=rate_limit_key)
jwt     = JWTManager()

# Swagger API object
api = Api(
    title='Courier Nepal Backend API',
    version='1.0',
    description='Core shipping & user-management endpoints',
    doc='/api/docs'
)
