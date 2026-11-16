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
    if config['RATELIMIT_STORAGE_URI'] == 'memory://' and not config.get('RATELIMIT_SINGLE_WORKER'):
        # In-memory limits live inside one process, so with several workers
        # each keeps its own counters and the effective limit is multiplied
        # by the worker count -- which is how a login lockout of eight
        # attempts quietly becomes twenty-four.
        #
        # Redis is the right answer and is not always on offer: a shared
        # cPanel plan usually has none, and this check refusing to start was
        # a deployment blocker with no way past it. So it can be
        # acknowledged instead, and the acknowledgement is specific: it
        # asserts that this application runs as exactly one process. Set
        # PassengerMaxPoolSize/`passenger_max_pool_size` to 1 for the app,
        # or raise a worker count anywhere and the limits silently loosen.
        raise RuntimeError(
            'Production requires shared rate-limit storage. Set '
            'RATELIMIT_STORAGE_URI to a redis:// or memcached:// URL. If '
            'this host has neither and the app runs as a single worker '
            '(the usual shared-hosting case), set RATELIMIT_SINGLE_WORKER=true '
            'to accept per-process limits -- and keep the worker count at 1, '
            'because every extra worker multiplies every limit.')
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


#: How long a public, non-personal GET may be cached. Short enough that
#: activating a campaign or correcting an FAQ shows up within minutes,
#: long enough that a burst of traffic does not become a burst of Python.
PUBLIC_CACHE_SECONDS = 180

#: Exact paths and prefixes whose GET responses contain nothing about the
#: person asking. Adding to this list means asserting that two different
#: visitors may be served the same bytes.
#:
#: Deliberately absent: /api/consent/ (the visitor's own decision),
#: /api/tracking/<no> (somebody's parcel), anything under /admin, and
#: every authenticated listing.
_CACHEABLE_EXACT = frozenset({
    '/api/consent/policy',
    '/api/faq/',
    '/api/faq',
    '/api/services/',
    '/api/services',
    '/api/content/pages',
    '/api/content/page',
    '/api/reviews/',
    '/api/reviews/summary',
    '/api/reviews/google',
    '/api/ads/serve',
    '/api/ads/notices',
    '/api/branches/',
    '/api/branches',
})

_CACHEABLE_PREFIXES = ('/api/geo/',)

#: Cookies that mean a person is signed in. Their presence disqualifies a
#: response from any cache, because the handler may have answered for them.
_AUTH_COOKIES = ('access_token', 'refresh_token', 'csrf_access_token',
                 'csrf_refresh_token')


def _is_publicly_cacheable(request, response):
    """Whether this response may be stored by a shared cache.

    Four conditions, all required. The request has to be a plain GET, the
    answer has to have been a success, the path has to be named above, and
    — the one that matters — the request must carry nothing that
    identifies a caller. An authenticated request to a public path still
    gets `no-store`, because the handler may have answered differently for
    that person and a shared cache cannot know that.
    """
    if request.method not in ('GET', 'HEAD'):
        return False
    if response.status_code != 200:
        return False
    if request.authorization or request.headers.get('Authorization'):
        return False
    path = request.path
    if not (path in _CACHEABLE_EXACT or path.startswith(_CACHEABLE_PREFIXES)):
        return False
    # A signed-in caller may get a different answer on the same path --
    # /api/branches/ returns the whole network to an administrator and only
    # the published ones to everybody else -- so an authenticated request is
    # never cached, anywhere, at any level.
    if any(name in request.cookies for name in _AUTH_COOKIES):
        return False
    return True


def _cache_scope(request):
    """`public` for an anonymous caller, `private` for an identified one.

    Most returning visitors carry the consent cookie, and treating that as
    disqualifying meant the cache only ever helped first-time visitors and
    crawlers. It is still not safe to put their copy in a shared cache --
    adverts vary by country and the consent cookie is an identifier -- but
    their own browser may keep it, which removes the repeat calls as they
    move between pages.
    """
    return 'private' if request.cookies else 'public'


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
            # `no-store` is the default and stays the default: shipment
            # records, customer details and admin listings must never sit in
            # a shared cache or on a visitor's disk.
            #
            # But the public site is now static HTML, and every page view
            # still called Flask five or six times for content that changes
            # about once a week — the service catalogue, the FAQ, the advert
            # slots, the cookie policy. On shared hosting that is a Python
            # process woken for every visitor to serve a constant.
            #
            # So: an allowlist, never a blocklist. A path has to be named
            # here to become cacheable, and a response is only cacheable
            # when nothing about the request identifies a person.
            if request.path.startswith('/api/ads/images/'):
                response.headers.pop('Pragma', None)
            elif _is_publicly_cacheable(request, response):
                response.headers['Cache-Control'] = (
                    f'{_cache_scope(request)}, max-age={PUBLIC_CACHE_SECONDS}, '
                    f'stale-while-revalidate={PUBLIC_CACHE_SECONDS * 4}')
                response.headers.pop('Pragma', None)
                # Country steers which adverts are returned, and language
                # steers the wording, so a shared cache must not serve one
                # visitor's copy to another in a different place.
                response.headers['Vary'] = 'Accept-Encoding, CF-IPCountry'
            else:
                response.headers['Cache-Control'] = 'no-store'
                response.headers['Pragma'] = 'no-cache'
        return response
