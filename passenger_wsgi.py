"""
Passenger WSGI entrypoint for cPanel's "Setup Python App".

cPanel/Phusion Passenger imports this file from the application root and looks
for a WSGI callable named `application`. We force the production config so the
app uses MySQL (from .env) and HTTPS-only Secure cookies.

Environment variables are read from the .env file in this directory
(config.py calls load_dotenv()) OR from the variables you set in the cPanel
Python App UI — either works.

To restart the app after a deploy: `touch tmp/restart.txt` in the app root.
"""
import os

os.environ.setdefault("FLASK_ENV", "production")

from app import create_app

application = create_app("production")
