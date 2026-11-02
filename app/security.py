import hmac
from flask import request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix


def validate_production(config):
    for name in ('SECRET_KEY', 'JWT_SECRET_KEY', 'BACKEND_PROXY_SECRET'):
        value = config.get(name, '') or ''
        if len(value) < 32 or value.lower().startswith(('change-me', 'test-')):
            raise RuntimeError(f'{name} must be an independently generated secret of at least 32 characters')
    if len({config[name] for name in ('SECRET_KEY', 'JWT_SECRET_KEY', 'BACKEND_PROXY_SECRET')}) != 3:
        raise RuntimeError('Production secrets must be different')
    if not config['SQLALCHEMY_DATABASE_URI'].startswith('mysql+pymysql://'):
        raise RuntimeError('Production requires configured MySQL credentials')
    if not config['FRONTEND_URL'].startswith('https://'):
        raise RuntimeError('FRONTEND_URL must use HTTPS in production')
    if config['RATELIMIT_STORAGE_URI'] == 'memory://':
        raise RuntimeError('Production requires shared rate-limit storage, such as Redis')


def install_security(app):
    hops = app.config['TRUSTED_PROXY_HOPS']
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=0, x_port=0, x_prefix=0)

    @app.before_request
    def protect_backend():
        if not request.path.startswith('/api'):
            if app.config.get('ENV_NAME') == 'production':
                return jsonify(msg='Not found'), 404
            return
        secret = app.config.get('BACKEND_PROXY_SECRET')
        if secret and not hmac.compare_digest(request.headers.get('X-Backend-Key', ''), secret):
            return jsonify(msg='Backend access denied'), 403
        if app.config.get('ENV_NAME') == 'production' and request.path.startswith(('/api/docs', '/api/swagger', '/api/mail-test')):
            return jsonify(msg='Not found'), 404

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if request.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response
