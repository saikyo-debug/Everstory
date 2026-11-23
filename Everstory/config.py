"""
Everstory - central configuration.

Everything that differs between your laptop and a production server is
read from environment variables here, so the rest of the code never has
to guess where it is running.

Environment variables (EVERSTORY_* names are preferred; the older
ALAALA_* names keep working so your current setup does not break):

    EVERSTORY_SECRET_KEY   Required. At least 32 random characters.
    EVERSTORY_ENV          "development" (default) or "production".
    EVERSTORY_DATA_DIR     Folder holding the database and uploads.
                           Defaults to the project folder.
    EVERSTORY_TRUST_PROXY  "1" when running behind nginx / a platform
                           proxy that sets X-Forwarded-* headers.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# The product name is defined once, here. Change this single line to
# rename the app everywhere in the interface.
APP_NAME = "Everstory"
APP_TAGLINE = "Every memory deserves to be remembered."
APP_VERSION = "1.0.0"


def load_env_file(path=None):
    """
    Load KEY=VALUE pairs from a local .env file into os.environ.

    Real environment variables always win over the file. This is a
    tiny built-in loader so no extra dependency is needed.
    """

    env_path = Path(path) if path else BASE_DIR / ".env"

    if not env_path.is_file():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if key:
            os.environ.setdefault(key, value)


load_env_file()


def env(*names, default=None):
    """Return the first environment variable that is set and non-empty."""

    for name in names:
        value = os.environ.get(name)

        if value is not None and value != "":
            return value

    return default


ENVIRONMENT = (
    env("EVERSTORY_ENV", "ALAALA_ENV", default="development")
).strip().lower()

IS_PRODUCTION = ENVIRONMENT == "production"

TRUST_PROXY = (
    env("EVERSTORY_TRUST_PROXY", "ALAALA_TRUST_PROXY", default="0")
).strip().lower() in ("1", "true", "yes", "on")

DATA_DIR = Path(
    env("EVERSTORY_DATA_DIR", "ALAALA_DATA_DIR", default=str(BASE_DIR))
).expanduser().resolve()

# The database keeps its original file name so existing data is found.
DEFAULT_DATABASE_PATH = DATA_DIR / "alaala.db"
DEFAULT_UPLOAD_DIR = DATA_DIR / "uploads"


def get_secret_key():
    """Return the configured secret key, or None if it is missing."""

    return env("EVERSTORY_SECRET_KEY", "ALAALA_SECRET_KEY")


# ----- Upload limits ------------------------------------------------

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_MEDIA_PER_ITEM = 10

# Hard ceiling for a single upload request (several files at once).
MAX_UPLOAD_REQUEST_BYTES = 80 * 1024 * 1024

# Ordinary form posts stay small.
MAX_FORM_REQUEST_BYTES = 5 * 1024 * 1024

# ----- Text limits --------------------------------------------------

MAX_TITLE_LENGTH = 150
MAX_ENTRY_LENGTH = 50_000
MAX_PASSWORD_LENGTH = 128
