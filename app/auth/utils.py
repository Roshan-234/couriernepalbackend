import re
from functools import wraps
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask import jsonify
from app.models.user import User
from app.extensions import db

def validate_password_strength(password: str):
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("One uppercase letter required")
    if not re.search(r"[a-z]", password):
        errors.append("One lowercase letter required")
    if not re.search(r"\d", password):
        errors.append("One digit required")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        errors.append("One special character required")
    return errors

def require_roles(*roles):
    def wrapper(fn):
        @wraps(fn)
        @jwt_required()
        def decorated(*args, **kwargs):
            uid = get_jwt_identity()
            user = User.query.get(uid)
            if not user:
                return {"msg": "User not found"}, 404
            if not any(r in [role.name for role in user.roles] for r in roles):
                return {"msg": "Insufficient permissions"}, 403
            return fn(*args, **kwargs)
        return decorated
    return wrapper
