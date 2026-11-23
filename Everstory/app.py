import calendar as calendar_module
import hashlib
import hmac
import math
import re
import secrets
import time

from datetime import date, datetime
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for
)

from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)

import config

from database import (
    initialize_database,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_password_hash_by_id,
    create_entry,
    get_entries_by_user,
    get_entries_page,
    get_entries_on_this_day,
    get_entry_by_id_and_user,
    get_entry_stats,
    get_media_summary_for_entries,
    list_media_for_entry,
    delete_entry_returning_media,
    set_entry_favorite,
    update_entry,
    delete_media_row,
    count_ready_capsules,
    get_voice_profile,
    get_user_preferences,
    update_user_theme_mode,
    update_user_personalization,
    update_user_password_hash,
    is_login_rate_limited,
    record_failed_login,
    clear_login_attempts
)

import database

from auth_utils import (
    current_user,
    is_safe_redirect_target,
    login_required
)

from media import (
    media_bp,
    flash_upload_result,
    remove_stored_files,
    save_uploaded_files
)

from capsules import capsules_bp
from assistant import assistant_bp
from voiceprint import voice_bp
from sync_api import sync_bp


# =========================================
# INITIALIZE FLASK
# =========================================

app = Flask(__name__)

SECRET_KEY = config.get_secret_key()

if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "A secure Flask secret key is missing or invalid. "
        "Please configure the EVERSTORY_SECRET_KEY (or the older "
        "ALAALA_SECRET_KEY) environment variable with at least 32 "
        "random characters. Run `python generate_secret_key.py` "
        "to create one."
    )

app.secret_key = SECRET_KEY

if config.TRUST_PROXY:
    # Behind nginx or a hosting platform the app sees the proxy's
    # address. This makes Flask use the real client address and the
    # real https scheme from the X-Forwarded-* headers.
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1
    )


# =========================================
# APPLICATION SECURITY CONFIGURATION
# =========================================

app.config.update(

    # Session cookie protection
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",

    # Secure cookies are switched on automatically in production
    # (EVERSTORY_ENV=production), which must run over HTTPS. Local
    # development over plain HTTP keeps this False.
    SESSION_COOKIE_SECURE=config.IS_PRODUCTION,

    # 30-minute permanent session lifetime
    PERMANENT_SESSION_LIFETIME=1800,

    # Refresh permanent session cookies on requests
    SESSION_REFRESH_EACH_REQUEST=True,

    # Limit ordinary request bodies to 5 MB. Routes that accept
    # photo/voice/video uploads raise this for themselves only.
    MAX_CONTENT_LENGTH=config.MAX_FORM_REQUEST_BYTES,

    # Where photo, voice and video attachments are stored.
    UPLOAD_FOLDER=str(config.DEFAULT_UPLOAD_DIR),

    JSON_AS_ASCII=False
)

# Routes allowed to receive large multipart uploads.
UPLOAD_ENDPOINTS = {
    "new_entry",
    "edit_entry",
    "media.add_entry_media",
    "capsules.create"
}

# Routes that must keep working without a signed-in user and must never
# be redirected to the login page (the service worker needs them).
SESSION_EXEMPT_ENDPOINTS = {
    "static",
    "service_worker",
    "web_manifest",
    "health",
    "offline_page"
}

# A password hash used to keep login timing the same for unknown
# email addresses (so response time does not reveal which emails exist).
_DUMMY_PASSWORD_HASH = generate_password_hash("everstory-timing-guard")

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

initialize_database()

app.register_blueprint(media_bp)
app.register_blueprint(capsules_bp)
app.register_blueprint(assistant_bp)
app.register_blueprint(voice_bp)
app.register_blueprint(sync_bp)


# =========================================
# STATIC FILE VERSIONING
# =========================================

@app.url_defaults
def add_static_file_version(endpoint, values):
    """
    Add ?v=<file modified time> to static URLs so browsers (and the
    offline cache) always fetch the newest CSS and JavaScript after an
    update instead of showing a stale copy.
    """

    if endpoint != "static" or "filename" not in values or "v" in values:
        return

    try:
        file_path = Path(app.static_folder) / values["filename"]
        values["v"] = int(file_path.stat().st_mtime)
    except (OSError, ValueError):
        pass


# =========================================
# HTTP SECURITY HEADERS
# =========================================

def build_content_security_policy():
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'"
    ]

    if config.IS_PRODUCTION:
        directives.append("upgrade-insecure-requests")

    return "; ".join(directives)


CONTENT_SECURITY_POLICY = build_content_security_policy()


@app.after_request
def add_security_headers(response):
    """
    Add baseline security headers to application responses.
    """

    response.headers["X-Content-Type-Options"] = "nosniff"

    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    response.headers["Referrer-Policy"] = (
        "strict-origin-when-cross-origin"
    )

    # The microphone is needed for voice notes and voice unlock; the
    # camera, location and other sensors are not used at all.
    response.headers["Permissions-Policy"] = (
        "microphone=(self), camera=(), geolocation=(), "
        "payment=(), usb=(), interest-cohort=()"
    )

    response.headers.setdefault(
        "Content-Security-Policy",
        CONTENT_SECURITY_POLICY
    )

    if config.IS_PRODUCTION and request.is_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

    # Pages contain private diary text: browsers and shared proxies
    # must not keep copies of them.
    if (
        response.mimetype == "text/html"
        and "Cache-Control" not in response.headers
    ):
        response.headers["Cache-Control"] = "private, no-store"

    return response


# =========================================
# ERROR HANDLERS
# =========================================

ERROR_MESSAGES = {
    400: ("Something went wrong with that request.",
          "Please go back, refresh the page and try again."),
    403: ("You don't have access to that.",
          "This page belongs to someone else or is not available."),
    404: ("We couldn't find that page.",
          "It may have been moved or deleted."),
    405: ("That action isn't allowed here.",
          "Please go back and use the buttons on the page."),
    413: ("That upload is too large.",
          "Please choose smaller files and try again."),
    429: ("Too many requests.",
          "Please wait a moment and try again."),
    500: ("Something broke on our side.",
          "Your memories are safe. Please try again in a moment."),
}


def render_error(error, status):
    headline, advice = ERROR_MESSAGES.get(
        status,
        ("Something went wrong.", "Please try again.")
    )

    description = getattr(error, "description", None)

    # Messages we wrote ourselves (for example the CSRF message) are
    # safe to show; Werkzeug's stock descriptions are not needed.
    if (
        status == 400
        and isinstance(description, str)
        and description.startswith("The security token")
    ):
        advice = description

    if request.path.startswith("/api/"):
        return jsonify({"error": headline, "detail": advice}), status

    if status == 413:
        # Plain text keeps this safe even when the request body was
        # never read.
        return (
            "The submitted request is too large. "
            "Please reduce its size and try again.",
            413
        )

    return render_template(
        "error.html",
        status=status,
        headline=headline,
        advice=advice
    ), status


def register_error_handler(status):
    @app.errorhandler(status)
    def handler(error, _status=status):
        return render_error(error, _status)

    return handler


for _status in (400, 403, 404, 405, 413, 429, 500):
    register_error_handler(_status)


# =========================================
# REQUEST SIZE LIMITS
# =========================================

@app.before_request
def allow_large_uploads_on_upload_routes():
    """
    Only the routes that really accept attachments may receive a big
    request body. Everything else keeps the small default limit.
    """

    if request.method == "POST" and request.endpoint in UPLOAD_ENDPOINTS:
        request.max_content_length = config.MAX_UPLOAD_REQUEST_BYTES


# =========================================
# CSRF PROTECTION
# =========================================

def generate_csrf_token():
    """
    Generate or retrieve a CSRF token stored in
    the current user's Flask session.
    """

    token = session.get("_csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token

    return token


app.jinja_env.globals["csrf_token"] = generate_csrf_token
app.jinja_env.globals["generate_csrf_token"] = generate_csrf_token


@app.before_request
def protect_csrf():
    """
    Validate CSRF tokens on all unsafe HTTP methods.
    """

    unsafe_methods = {
        "POST",
        "PUT",
        "PATCH",
        "DELETE"
    }

    if request.method not in unsafe_methods:
        return None

    stored_token = session.get("_csrf_token")

    submitted_token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
    )

    if (
        not isinstance(stored_token, str)
        or not isinstance(submitted_token, str)
        or not stored_token
        or not submitted_token
        or not hmac.compare_digest(
            stored_token,
            submitted_token
        )
    ):
        abort(
            400,
            description=(
                "The security token is missing or invalid. "
                "Please refresh the page and try again."
            )
        )

    return None


# =========================================
# GLOBAL SESSION SECURITY
# =========================================

def end_session(message, category="error"):
    """Clear the session and send the person to log in again."""

    session.clear()

    if request.path.startswith("/api/"):
        return jsonify({"error": message}), 401

    flash(message, category)

    return redirect(url_for("login"))


@app.before_request
def validate_user_session():
    """
    Validate the logged-in user's session version
    and enforce a 30-minute inactivity timeout.
    """

    if request.endpoint in SESSION_EXEMPT_ENDPOINTS:
        return None

    if "user_id" not in session:
        return None

    current_time = time.time()
    last_activity = session.get("_last_activity")

    # Reject missing, invalid, or boolean timestamps.
    if (
        isinstance(last_activity, bool)
        or not isinstance(last_activity, (int, float))
    ):
        return end_session(
            "Your session has expired. Please log in again."
        )

    # Reject timestamps unexpectedly far into the future.
    if last_activity > current_time + 60:
        return end_session(
            "Your session could not be verified. Please log in again."
        )

    # Enforce the 30-minute inactivity timeout.
    if current_time - last_activity >= 1800:
        return end_session(
            "Your session expired due to inactivity. "
            "Please log in again."
        )

    try:
        user_id = session.get("user_id")
        stored_session_version = session.get("session_version")

        user = get_user_by_id(user_id)

        if user is None:
            return end_session(
                "Your account could not be found. Please log in again."
            )

        current_session_version = user["session_version"]

        if (
            stored_session_version is None
            or stored_session_version != current_session_version
        ):
            return end_session(
                "Your session has expired or your password was changed. "
                "Please log in again."
            )

        # Remember the user for the rest of this request so routes and
        # templates do not each query the database again.
        g.current_user = user

        # Refresh activity after successful validation.
        session["_last_activity"] = current_time

        return None

    except Exception:
        app.logger.exception(
            "Could not validate the user's session."
        )

        return end_session(
            "We couldn't verify your session. Please log in again."
        )


# =========================================
# TEMPLATE HELPERS
# =========================================

def get_theme():
    """
    The person's appearance settings, ready to be written onto the
    <html> tag by the server. Doing it on the server means every page
    paints in the right theme immediately, with no flash of the wrong
    colours while JavaScript loads.
    """

    if "theme" in g:
        return g.theme

    theme = {
        "mode": "system",
        "season": "classic",
        "font": "modern",
        "text_size": "medium",
        "reduced_motion": "false"
    }

    user = current_user()

    if user is not None:
        try:
            preferences = get_user_preferences(user["id"])

            if preferences:
                theme = {
                    "mode": preferences["theme_mode"],
                    "season": preferences["seasonal_theme"],
                    "font": preferences["font_style"],
                    "text_size": preferences["text_size"],
                    "reduced_motion": (
                        "true" if preferences["reduced_animations"]
                        else "false"
                    )
                }

        except Exception:
            app.logger.exception(
                "Could not load the user's theme settings."
            )

    # "system" is resolved in the browser before the first paint; the
    # server renders the light palette as the starting point.
    theme["data_theme"] = "dark" if theme["mode"] == "dark" else "light"

    g.theme = theme

    return theme


@app.context_processor
def inject_globals():

    theme = get_theme()

    return {
        "app_name": config.APP_NAME,
        "app_tagline": config.APP_TAGLINE,
        "theme": theme,
        "theme_mode": theme["mode"],
        "signed_in_user": current_user(),
    }


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


@app.template_filter("pretty_datetime")
def pretty_datetime(value):
    """2026-11-21 22:07:02 -> November 21, 2026 · 10:07 PM"""

    parsed = parse_timestamp(value)

    if parsed is None:
        return str(value or "")

    return (
        parsed.strftime("%B %d, %Y · %I:%M %p")
        .replace(" 0", " ")
        .replace("· 0", "· ")
    )


@app.template_filter("pretty_date")
def pretty_date(value):
    parsed = parse_timestamp(value)

    if parsed is None:
        return str(value or "")

    return parsed.strftime("%B %d, %Y").replace(" 0", " ")


@app.template_filter("excerpt")
def excerpt(text, limit=320):
    """Shorten long text at a word boundary."""

    text = text or ""

    if len(text) <= limit:
        return text

    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:!? \n")

    return (cut or text[:limit]) + "…"


@app.template_filter("is_long")
def is_long(text, limit=320):
    return len(text or "") > limit


# =========================================
# HOMEPAGE
# =========================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================
# REGISTRATION
# =========================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        full_name = request.form.get(
            "full_name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        if (
            not full_name
            or not email
            or not password
            or not confirm_password
        ):
            flash(
                "Please complete all fields.",
                "error"
            )

            return redirect(url_for("register"))

        if len(full_name) > 100:
            flash(
                "Your name must not exceed 100 characters.",
                "error"
            )

            return redirect(url_for("register"))

        if len(email) > 254 or not EMAIL_PATTERN.match(email):
            flash(
                "Please enter a valid email address.",
                "error"
            )

            return redirect(url_for("register"))

        if len(password) < 8:
            flash(
                "Your password must contain at least 8 characters.",
                "error"
            )

            return redirect(url_for("register"))

        if len(password) > config.MAX_PASSWORD_LENGTH:
            flash(
                "Your password must not exceed "
                f"{config.MAX_PASSWORD_LENGTH} characters.",
                "error"
            )

            return redirect(url_for("register"))

        if password != confirm_password:
            flash(
                "Your passwords do not match.",
                "error"
            )

            return redirect(url_for("register"))

        if get_user_by_email(email):
            flash(
                "An account with this email already exists.",
                "error"
            )

            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        try:
            create_user(
                full_name,
                email,
                password_hash
            )

        except Exception:
            app.logger.exception(
                "Account registration failed."
            )

            flash(
                "We couldn't create your account. Please try again.",
                "error"
            )

            return redirect(url_for("register"))

        flash(
            f"Your {config.APP_NAME} account has been created! "
            "You can now log in.",
            "success"
        )

        return redirect(url_for("login"))

    return render_template("register.html")


# =========================================
# LOGIN
# =========================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        next_target = request.form.get("next", "")

        ip_address = request.remote_addr or "unknown"

        try:
            rate_limited = is_login_rate_limited(
                email,
                ip_address,
                max_attempts=5,
                window_seconds=900,
                max_ip_attempts=20
            )

        except Exception:
            app.logger.exception(
                "Could not check login rate limits."
            )

            flash(
                "We couldn't process your login right now. "
                "Please try again later.",
                "error"
            )

            return redirect(url_for("login"))

        if rate_limited:
            flash(
                "Too many unsuccessful login attempts. "
                "Please wait 15 minutes before trying again.",
                "error"
            )

            return redirect(url_for("login"))

        try:
            user = get_user_by_email(email)

            # Absurdly long passwords are rejected before hashing so
            # they cannot be used to tie up the server.
            password_is_plausible = len(password) <= 1024

            if user and password_is_plausible:
                password_correct = check_password_hash(
                    user["password_hash"],
                    password
                )
            else:
                # Spend the same time as a real check, so an unknown
                # email cannot be told apart by how fast we answer.
                if password_is_plausible:
                    check_password_hash(_DUMMY_PASSWORD_HASH, password)

                password_correct = False

            if user and password_correct:

                clear_login_attempts(
                    email,
                    ip_address
                )

                session.clear()

                session["user_id"] = user["id"]

                session["session_version"] = user["session_version"]

                session.permanent = True

                session["_last_activity"] = time.time()

                flash(
                    f"Welcome back to {config.APP_NAME}!",
                    "success"
                )

                if is_safe_redirect_target(next_target):
                    return redirect(next_target)

                return redirect(url_for("dashboard"))

            record_failed_login(
                email,
                ip_address
            )

        except Exception:
            app.logger.exception(
                "Login authentication failed."
            )

            flash(
                "We couldn't process your login right now. "
                "Please try again later.",
                "error"
            )

            return redirect(url_for("login"))

        flash(
            "Incorrect email or password.",
            "error"
        )

        return redirect(url_for("login"))

    next_target = request.args.get("next", "")

    if not is_safe_redirect_target(next_target):
        next_target = ""

    return render_template("login.html", next_target=next_target)


# =========================================
# PERSONAL DASHBOARD
# =========================================

@app.route("/dashboard")
@login_required
def dashboard():

    user = current_user()

    stats = get_entry_stats(user["id"])

    recent_entries, _total = get_entries_page(
        user["id"],
        page=1,
        per_page=3
    )

    today = database.get_local_time()

    on_this_day = get_entries_on_this_day(
        user["id"],
        today.strftime("%m-%d"),
        today.year
    )

    return render_template(
        "dashboard.html",
        user=user,
        stats=stats,
        entries=recent_entries,
        media_summary=get_media_summary_for_entries(
            user["id"],
            [entry["id"] for entry in recent_entries]
        ),
        on_this_day=on_this_day,
        capsules_ready=count_ready_capsules(user["id"])
    )


# =========================================
# SETTINGS PAGE
# =========================================

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_view():

    user = current_user()

    if request.method == "POST":

        form_type = request.form.get(
            "form_type",
            ""
        ).strip().lower()

        if form_type == "appearance" or (
            not form_type
            and "theme_mode" in request.form
        ):

            theme_mode = request.form.get(
                "theme_mode",
                ""
            ).strip().lower()

            allowed_modes = [
                "light",
                "dark",
                "system"
            ]

            if theme_mode not in allowed_modes:
                flash(
                    "Please select a valid appearance mode.",
                    "error"
                )

            else:
                try:
                    saved = update_user_theme_mode(
                        user["id"],
                        theme_mode
                    )

                    if saved:
                        flash(
                            "Your appearance settings have been saved! 💜",
                            "success"
                        )

                    else:
                        flash(
                            "We couldn't save your appearance settings.",
                            "error"
                        )

                except Exception:
                    app.logger.exception(
                        "Appearance settings update failed."
                    )

                    flash(
                        "Something went wrong while saving your "
                        "appearance settings.",
                        "error"
                    )

        elif form_type == "preferences" or (
            "seasonal_theme" in request.form
        ):

            seasonal_theme = request.form.get(
                "seasonal_theme",
                "classic"
            ).strip().lower()

            font_style = request.form.get(
                "font_style",
                "modern"
            ).strip().lower()

            text_size = request.form.get(
                "text_size",
                "medium"
            ).strip().lower()

            reduced_animations_value = request.form.get(
                "reduced_animations",
                "0"
            ).strip().lower()

            reduced_animations = reduced_animations_value in (
                "1",
                "true",
                "on",
                "yes"
            )

            try:
                saved = update_user_personalization(
                    user["id"],
                    seasonal_theme,
                    font_style,
                    text_size,
                    reduced_animations
                )

                if saved:
                    flash(
                        "Your personalization preferences have "
                        "been saved! 💜",
                        "success"
                    )

                else:
                    flash(
                        "We couldn't save your preferences. "
                        "Please try again.",
                        "error"
                    )

            except ValueError:
                flash(
                    "One or more selected preferences are invalid.",
                    "error"
                )

            except Exception:
                app.logger.exception(
                    "Personalization preferences update failed."
                )

                flash(
                    "Something went wrong while saving your preferences.",
                    "error"
                )

        else:
            flash(
                "We couldn't identify which settings you wanted to save.",
                "error"
            )

        return redirect(url_for("settings_view"))

    preferences = get_user_preferences(user["id"])
    voice_profile = get_voice_profile(user["id"])

    return render_template(
        "settings.html",
        user=user,
        preferences=preferences,
        voice_profile=voice_profile
    )


# =========================================
# CHANGE PASSWORD
# =========================================

@app.route("/settings/change-password", methods=["POST"])
@login_required
def change_password():

    user = current_user()

    current_password = request.form.get(
        "current_password",
        ""
    )

    new_password = request.form.get(
        "new_password",
        ""
    )

    confirm_password = request.form.get(
        "confirm_password",
        ""
    )

    if (
        not current_password
        or not new_password
        or not confirm_password
    ):
        flash(
            "Please complete all password fields.",
            "error"
        )

        return redirect(url_for("settings_view"))

    # Someone using a borrowed, unlocked device must not be able to
    # guess the current password without limit.
    attempt_key = f"pwchange:{user['id']}"
    ip_address = request.remote_addr or "unknown"

    if is_login_rate_limited(
        attempt_key,
        ip_address,
        max_attempts=5,
        window_seconds=900,
        max_ip_attempts=30
    ):
        flash(
            "Too many unsuccessful attempts. "
            "Please wait 15 minutes before trying again.",
            "error"
        )

        return redirect(url_for("settings_view"))

    stored_hash = get_password_hash_by_id(user["id"])

    if (
        len(current_password) > 1024
        or not stored_hash
        or not check_password_hash(stored_hash, current_password)
    ):
        record_failed_login(attempt_key, ip_address)

        flash(
            "Your current password is incorrect.",
            "error"
        )

        return redirect(url_for("settings_view"))

    if len(new_password) < 8:
        flash(
            "Your new password must contain at least 8 characters.",
            "error"
        )

        return redirect(url_for("settings_view"))

    if len(new_password) > config.MAX_PASSWORD_LENGTH:
        flash(
            "Your new password must not exceed "
            f"{config.MAX_PASSWORD_LENGTH} characters.",
            "error"
        )

        return redirect(url_for("settings_view"))

    if new_password != confirm_password:
        flash(
            "Your new passwords do not match.",
            "error"
        )

        return redirect(url_for("settings_view"))

    if current_password == new_password:
        flash(
            "Your new password must be different from "
            "your current password.",
            "error"
        )

        return redirect(url_for("settings_view"))

    try:
        new_password_hash = generate_password_hash(
            new_password
        )

        new_session_version = update_user_password_hash(
            user["id"],
            new_password_hash
        )

        if new_session_version is None:
            flash(
                "We couldn't update your password. Please try again.",
                "error"
            )

            return redirect(url_for("settings_view"))

        clear_login_attempts(attempt_key, ip_address)

        session["session_version"] = new_session_version

        flash(
            "Your password has been changed successfully! "
            "Other devices will need to log in again. 💜",
            "success"
        )

        return redirect(url_for("settings_view"))

    except Exception:
        app.logger.exception(
            "Password change failed."
        )

        flash(
            "Something went wrong while changing your password. "
            "Please try again.",
            "error"
        )

        return redirect(url_for("settings_view"))


# =========================================
# GLOBAL PREFERENCES API
# =========================================

@app.route("/api/preferences", methods=["GET"])
@login_required
def get_preferences_api():

    try:
        preferences = get_user_preferences(
            session["user_id"]
        )

        if preferences is None:
            return jsonify({
                "error": "Preferences could not be found."
            }), 404

        response = jsonify({
            "theme_mode": preferences["theme_mode"],
            "seasonal_theme": preferences["seasonal_theme"],
            "font_style": preferences["font_style"],
            "text_size": preferences["text_size"],
            "reduced_animations": bool(
                preferences["reduced_animations"]
            )
        })

        response.headers["Cache-Control"] = "no-store"

        return response

    except Exception:
        app.logger.exception(
            "Could not retrieve the user's preferences."
        )

        return jsonify({
            "error": "Could not load preferences."
        }), 500


# =========================================
# CALENDAR PAGE
# =========================================

@app.route("/calendar")
@login_required
def calendar_view():

    user = current_user()

    today = date.today()

    try:
        year = int(
            request.args.get("year", today.year)
        )

        month = int(
            request.args.get("month", today.month)
        )

        if not 1 <= month <= 12 or not 1 <= year <= 9999:
            raise ValueError

    except (ValueError, TypeError):
        year = today.year
        month = today.month

    selected_date_value = request.args.get(
        "date",
        today.isoformat()
    )

    try:
        selected_date = date.fromisoformat(
            selected_date_value
        )

        if (
            selected_date.year != year
            or selected_date.month != month
        ):
            selected_date = date(year, month, 1)

    except (ValueError, TypeError):
        selected_date = date(year, month, 1)

    user_entries = get_entries_by_user(user["id"])

    entries_by_date = {}

    for entry in user_entries:
        entry_datetime = parse_timestamp(entry["created_at"])

        if entry_datetime is None:
            app.logger.warning(
                "Could not read the creation date of diary entry %s.",
                entry["id"]
            )
            continue

        entries_by_date.setdefault(
            entry_datetime.date().isoformat(),
            []
        ).append(entry)

    days_with_entries = list(
        entries_by_date.keys()
    )

    calendar_builder = calendar_module.Calendar(
        firstweekday=6
    )

    weeks = calendar_builder.monthdayscalendar(
        year,
        month
    )

    previous_month = month - 1
    previous_year = year

    if previous_month == 0:
        previous_month = 12
        previous_year -= 1

    next_month = month + 1
    next_year = year

    if next_month == 13:
        next_month = 1
        next_year += 1

    selected_entries = entries_by_date.get(
        selected_date.isoformat(),
        []
    )

    month_entry_count = sum(
        len(items)
        for day_key, items in entries_by_date.items()
        if day_key.startswith(f"{year:04d}-{month:02d}-")
    )

    return render_template(
        "calendar.html",
        user=user,
        weeks=weeks,
        year=year,
        month=month,
        month_name=calendar_module.month_name[month],
        previous_month=previous_month,
        previous_year=previous_year,
        next_month=next_month,
        next_year=next_year,
        today=today,
        selected_date=selected_date,
        entries_by_date=entries_by_date,
        days_with_entries=days_with_entries,
        selected_entries=selected_entries,
        month_entry_count=month_entry_count
    )


# =========================================
# DIARY ENTRIES: LIST
# =========================================

ENTRIES_PER_PAGE = 10


@app.route("/entries")
@login_required
def entries():

    user = current_user()

    query = request.args.get("q", "").strip()[:100]
    favorites_only = request.args.get("filter") == "favorites"

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    page_entries, total = get_entries_page(
        user["id"],
        query=query,
        favorites_only=favorites_only,
        page=page,
        per_page=ENTRIES_PER_PAGE
    )

    total_pages = max(1, math.ceil(total / ENTRIES_PER_PAGE))

    # A page number beyond the end shows the last page instead.
    if page > total_pages:
        page = total_pages

        page_entries, total = get_entries_page(
            user["id"],
            query=query,
            favorites_only=favorites_only,
            page=page,
            per_page=ENTRIES_PER_PAGE
        )

    return render_template(
        "entries.html",
        user=user,
        entries=page_entries,
        media_summary=get_media_summary_for_entries(
            user["id"],
            [entry["id"] for entry in page_entries]
        ),
        query=query,
        favorites_only=favorites_only,
        page=page,
        total_pages=total_pages,
        total=total,
        has_any_entries=(
            total > 0 or bool(query) or favorites_only
            or get_entry_stats(user["id"])["total"] > 0
        )
    )


# =========================================
# DIARY ENTRIES: VALIDATION
# =========================================

def validate_entry_text(title, content):
    """Return an error message, or None when the text is fine."""

    if not title or not content:
        return "Please enter both a title and your memory."

    if len(title) > config.MAX_TITLE_LENGTH:
        return (
            f"Your title must not exceed {config.MAX_TITLE_LENGTH} "
            "characters."
        )

    if len(content) > config.MAX_ENTRY_LENGTH:
        return (
            "Your memory is too long. Please keep it under "
            f"{config.MAX_ENTRY_LENGTH:,} characters, or split it "
            "into two memories."
        )

    return None


def entry_form_context(user, **extra):
    context = {
        "user": user,
        "max_title": config.MAX_TITLE_LENGTH,
        "max_files": config.MAX_MEDIA_PER_ITEM,
    }

    context.update(extra)

    return context


# =========================================
# DIARY ENTRIES: CREATE
# =========================================

@app.route("/entries/new", methods=["GET", "POST"])
@login_required
def new_entry():

    user = current_user()

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        content = request.form.get(
            "content",
            ""
        ).strip()

        problem = validate_entry_text(title, content)

        if problem:
            flash(problem, "error")

            return render_template(
                "entry_form.html",
                **entry_form_context(user, entry=None, media=[])
            )

        try:
            entry_id = create_entry(
                user["id"],
                title,
                content
            )

        except Exception:
            app.logger.exception(
                "Diary entry creation failed."
            )

            flash(
                "We couldn't save your memory. Please try again.",
                "error"
            )

            return render_template(
                "entry_form.html",
                **entry_form_context(user, entry=None, media=[])
            )

        files = request.files.getlist("media")

        if any(storage and storage.filename for storage in files):
            saved, errors = save_uploaded_files(
                files,
                user["id"],
                entry_id=entry_id
            )

            flash_upload_result(saved, errors)

        flash(
            "Your memory has been saved successfully! 💜",
            "success"
        )

        return redirect(url_for("view_entry", entry_id=entry_id))

    return render_template(
        "entry_form.html",
        **entry_form_context(user, entry=None, media=[])
    )


# =========================================
# DIARY ENTRIES: READ
# =========================================

@app.route("/entries/<int:entry_id>")
@login_required
def view_entry(entry_id):

    user = current_user()

    entry = get_entry_by_id_and_user(entry_id, user["id"])

    if entry is None:
        abort(404)

    return render_template(
        "entry_detail.html",
        user=user,
        entry=entry,
        media=list_media_for_entry(entry_id, user["id"])
    )


# =========================================
# DIARY ENTRIES: EDIT
# =========================================

@app.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id):

    user = current_user()

    entry = get_entry_by_id_and_user(entry_id, user["id"])

    if entry is None:
        abort(404)

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        problem = validate_entry_text(title, content)

        if problem:
            flash(problem, "error")

            return render_template(
                "entry_form.html",
                **entry_form_context(
                    user,
                    entry=entry,
                    media=list_media_for_entry(entry_id, user["id"])
                )
            )

        try:
            update_entry(entry_id, user["id"], title, content)

        except Exception:
            app.logger.exception("Diary entry update failed.")

            flash(
                "We couldn't save your changes. Please try again.",
                "error"
            )

            return redirect(url_for("edit_entry", entry_id=entry_id))

        # Attachments ticked for removal.
        for raw_id in request.form.getlist("remove_media"):
            try:
                media_id = int(raw_id)
            except ValueError:
                continue

            existing = database.get_media_by_id(media_id)

            if (
                existing is None
                or existing["entry_id"] != entry_id
                or existing["user_id"] != user["id"]
            ):
                continue

            removed = delete_media_row(media_id, user["id"])

            if removed is not None:
                remove_stored_files(user["id"], [removed["stored_name"]])

        files = request.files.getlist("media")

        if any(storage and storage.filename for storage in files):
            saved, errors = save_uploaded_files(
                files,
                user["id"],
                entry_id=entry_id
            )

            flash_upload_result(saved, errors)

        flash("Your memory has been updated! 💜", "success")

        return redirect(url_for("view_entry", entry_id=entry_id))

    return render_template(
        "entry_form.html",
        **entry_form_context(
            user,
            entry=entry,
            media=list_media_for_entry(entry_id, user["id"])
        )
    )


# =========================================
# DIARY ENTRIES: DELETE AND FAVORITE
# =========================================

@app.route("/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def remove_entry(entry_id):

    user = current_user()

    stored_names = delete_entry_returning_media(entry_id, user["id"])

    if stored_names is None:
        abort(404)

    remove_stored_files(user["id"], stored_names)

    flash("Your memory has been deleted.", "success")

    return redirect(url_for("entries"))


@app.route("/entries/<int:entry_id>/favorite", methods=["POST"])
@login_required
def toggle_favorite(entry_id):

    user = current_user()

    entry = get_entry_by_id_and_user(entry_id, user["id"])

    if entry is None:
        abort(404)

    new_value = not bool(entry["is_favorite"])

    set_entry_favorite(entry_id, user["id"], new_value)

    wants_json = (
        "application/json" in request.headers.get("Accept", "")
        and "text/html" not in request.headers.get("Accept", "")
    )

    if wants_json:
        return jsonify({"ok": True, "is_favorite": new_value})

    target = request.form.get("next", "")

    if is_safe_redirect_target(target):
        return redirect(target)

    return redirect(url_for("view_entry", entry_id=entry_id))


# =========================================
# LOGOUT
# =========================================

@app.route("/logout", methods=["POST"])
def logout():

    session.clear()

    flash(
        "You have been logged out.",
        "success"
    )

    return redirect(url_for("home"))


# =========================================
# OFFLINE SUPPORT (INSTALLABLE APP + SERVICE WORKER)
# =========================================

SERVICE_WORKER_ASSETS = [
    "css/dashboard.css",
    "css/theme.css",
    "css/app.css",
    "js/theme-boot.js",
    "js/theme.js",
    "js/offline-store.js",
    "js/offline-page.js",
    "icons/icon-192.png",
]


@app.route("/offline")
def offline_page():
    """
    The page shown when the network is unavailable. It contains no
    private data; the browser fills it from the copy kept on this device.
    """

    response = make_response(render_template("offline.html"))
    response.headers["Cache-Control"] = "no-cache"

    return response


@app.route("/sw.js")
def service_worker():
    assets = [
        url_for("static", filename=name)
        for name in SERVICE_WORKER_ASSETS
    ]

    # The cache name changes whenever any shell file changes (the
    # asset URLs carry a modified-time version), so users never keep
    # stale copies after an update.
    cache_version = hashlib.sha1(
        "|".join(assets + [config.APP_VERSION]).encode("utf-8")
    ).hexdigest()[:12]

    response = make_response(
        render_template(
            "sw.js",
            cache_version=cache_version,
            offline_url=url_for("offline_page"),
            assets=assets
        )
    )

    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"

    return response


@app.route("/manifest.webmanifest")
def web_manifest():
    manifest = {
        "name": config.APP_NAME,
        "short_name": config.APP_NAME,
        "description": config.APP_TAGLINE,
        "start_url": url_for("dashboard"),
        "scope": "/",
        "display": "standalone",
        "background_color": "#F8F8F5",
        "theme_color": "#958CE8",
        "icons": [
            {
                "src": url_for("static", filename="icons/icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": url_for("static", filename="icons/icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": url_for(
                    "static", filename="icons/icon-maskable-512.png"
                ),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ]
    }

    response = jsonify(manifest)
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"

    return response


# =========================================
# HEALTH CHECK (for hosting platforms and uptime monitors)
# =========================================

@app.route("/healthz")
def health():
    try:
        with database.get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception:
        app.logger.exception("Health check failed.")
        return jsonify({"status": "error"}), 503

    return jsonify({"status": "ok", "version": config.APP_VERSION})


# =========================================
# RUN APPLICATION
# =========================================

if __name__ == "__main__":

    # Development server only. In production run the app with a real
    # WSGI server (see DEPLOYMENT.md), not this block.
    app.run(
        host="127.0.0.1" if config.IS_PRODUCTION else "0.0.0.0",
        port=5000,
        debug=False
    )
