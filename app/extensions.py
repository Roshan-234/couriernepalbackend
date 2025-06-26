from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_jwt_extended import JWTManager
from flask_restx import Api
from flask_mail import Mail

db      = SQLAlchemy()
migrate = Migrate()
bcrypt  = Bcrypt()
mail = Mail()
cors = CORS(resources={
    r"/api/*": {
        "origins": "*",
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Authorization"]
    }
})
limiter = Limiter(key_func=get_remote_address)
jwt     = JWTManager()

# Swagger API object
api = Api(
    title='Courier Nepal Backend API',
    version='1.0',
    description='Core shipping & user-management endpoints',
    doc='/api/docs'
)
