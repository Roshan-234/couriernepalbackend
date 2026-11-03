import os
from app import create_app

env = os.getenv("FLASK_ENV", "default")
app = create_app(env)

if __name__ == "__main__":
    # Bind to loopback unless told otherwise. The default config is
    # DevelopmentConfig, which turns on DEBUG, and DEBUG on 0.0.0.0 publishes
    # the Werkzeug debugger — a remote shell — to every interface. Opening
    # this up has to be a deliberate act.
    host = os.getenv("DEV_SERVER_HOST", "127.0.0.1")
    if app.config.get("DEBUG") and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            "Refusing to serve a debug build on %s: the Werkzeug debugger "
            "allows arbitrary code execution. Set FLASK_ENV=production, or "
            "keep DEV_SERVER_HOST on loopback." % host
        )
    # threaded=True so the dev server can handle the concurrent API calls the
    # dashboard fires on load. Production should run behind a real WSGI server
    # (gunicorn/waitress), not this.
    app.run(host=host, port=int(os.getenv("PORT", "5000")), threaded=True)
