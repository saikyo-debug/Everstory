"""
Prints a strong random secret key for EVERSTORY_SECRET_KEY.

    python generate_secret_key.py

Copy the printed line into your .env file (see .env.example). Keep it
private, and never change it once people are using the app: changing it
signs everyone out.
"""

import secrets

print("EVERSTORY_SECRET_KEY=" + secrets.token_urlsafe(48))
