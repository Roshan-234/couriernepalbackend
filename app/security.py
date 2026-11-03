import hmac
from flask import g, request, jsonify
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
    # The API is reached through the frontend's /api proxy, so without this
    # every request carries the proxy's address: rate limits collapse into one
    # shared bucket and the audit log records the wrong source for everything.
    if not config.get('TRUSTED_PROXY_HOPS'):
        raise RuntimeError(
            'TRUSTED_PROXY_HOPS must be set in production (normally 1) so the '
            'real client address is read from X-Forwarded-For'
        )
    # Secrets held in the database are encrypted with this key. Deriving it
    # from SECRET_KEY is a development convenience; in production rotating the
    # session secret would then silently make every stored credential
    # unreadable.
    if not config.get('FIELD_ENCRYPTION_KEY') and not config.get('FIELD_ENCRYPTION_KEYS'):
        raise RuntimeError(
            'FIELD_ENCRYPTION_KEY must be set in production so carrier and '
            'gateway credentials do not depend on SECRET_KEY'
        )


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
            # Flagged so the audit middleware does not write a row for traffic
            # that never reached the application. This hook runs before the
            # rate limiter, so without the flag an unauthenticated flood would
            # be an unbounded stream of inserts.
            g.rejected_at_edge = True
            return jsonify(msg='Backend access denied'), 403
        if app.config.get('ENV_NAME') == 'production' and request.path.startswith(('/api/docs', '/api/swagger')):
            g.rejected_at_edge = True
            return jsonify(msg='Not found'), 404

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), payment=(), usb=(), '
            'interest-cohort=()'
        )
        response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
        response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
        # Apart from the Swagger UI (development only), this service answers
        # with JSON or a generated spreadsheet. A policy that permits nothing
        # at all turns any future reflected content into inert text rather
        # than something a browser will run. The docs page is real HTML and
        # needs its own scripts, so it is left out.
        if not response.mimetype.startswith('text/html'):
            response.headers['Content-Security-Policy'] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'none'; sandbox"
            )
        if app.config.get('ENV_NAME') == 'production':
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains'
            )
        if request.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
        return response
