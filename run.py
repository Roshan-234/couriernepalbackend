import os
from app import create_app

env = os.getenv("FLASK_ENV", "default")
app = create_app(env)

if __name__ == "__main__":
    # threaded=True so the dev server can handle the concurrent API calls the
    # dashboard fires on load. Production should run behind a real WSGI server
    # (gunicorn/waitress), not this.
    app.run(host="0.0.0.0", port=5000, threaded=True)
