"""
WSGI entry point for production servers.

    gunicorn wsgi:app -c gunicorn.conf.py          (Linux / macOS / Docker)
    waitress-serve --listen=0.0.0.0:8000 wsgi:app  (Windows)

Do not use `python app.py` in production: that is Flask's development
server, which is not built for real traffic.
"""

from app import app

# Some hosting platforms look for the conventional name "application".
application = app
