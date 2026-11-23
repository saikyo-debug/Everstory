"""
Gunicorn settings for Everstory (Linux / macOS / Docker).

    gunicorn wsgi:app -c gunicorn.conf.py

Everything can be overridden with environment variables, so the same
file works on a laptop, a VPS and a hosting platform.
"""

import os

# Hosting platforms (Render, Railway, Heroku ...) tell the app which
# port to use through PORT.
# Behind nginx on the same machine, use GUNICORN_BIND=127.0.0.1:8000 so the
# app is not reachable from outside except through nginx.
bind = os.environ.get(
    "GUNICORN_BIND", f"0.0.0.0:{os.environ.get('PORT', '8000')}"
)

# SQLite handles many readers and one writer at a time. A small number
# of workers with a few threads each is the right size for a personal
# diary; more workers only add lock contention.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Generous, so a slow phone connection can finish uploading a video.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Recycle workers now and then to keep memory use steady.
max_requests = 1000
max_requests_jitter = 100

# Logs go to the console; Docker, systemd and hosting platforms collect them.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# Only believe X-Forwarded-* headers from your own reverse proxy.
# Behind nginx on the same machine the default (127.0.0.1) is correct.
# In Docker or on a platform, set FORWARDED_ALLOW_IPS to the proxy address
# (or "*" only when nothing else can reach the app directly).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
