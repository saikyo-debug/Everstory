"""
Everstory - voice unlock (voice biometrics).

WHAT THIS IS
    An optional extra check. After you record a short spoken passphrase
    five times, Everstory remembers a small set of numbers describing
    how your voice sounds (a "voice template"). Later, sensitive actions
    - such as opening a Time Capsule - ask you to speak again, and the
    new recording is compared with the template.

WHAT THIS IS NOT
    It is not a replacement for your password, and it is not strong
    enough to protect an account by itself:

    * It is a simple statistical voice match, not a trained speaker
      recognition model. It can wrongly reject you (noisy room, a cold)
      and, rarely, wrongly accept someone with a similar voice.
    * A recording of your voice played back to the microphone can fool
      any system of this kind.

    That is why it is only a step-up check on top of an already signed-
    in session, and why there is ALWAYS a password fallback. Nobody can
    be locked out by it.

PRIVACY
    * The browser analyses the audio itself. Only a list of 28 numbers
      is sent to the server - never the recording.
    * Only the averaged template is stored. Delete it any time from
      Settings.
    * Enrolling or deleting a voice profile needs your password.
"""

import math
import time
import json

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)

from werkzeug.security import check_password_hash

import database
from auth_utils import (
    current_user,
    is_safe_redirect_target,
    login_required
)


voice_bp = Blueprint("voice", __name__)

# 13 MFCC means + 13 MFCC standard deviations + 2 pitch values.
FEATURE_COUNT = 28

# Each of the 28 numbers is compared in units of its normal "take-to-take
# wobble" (FEATURE_SCALES), and numbers that tell people apart better get
# more say (FEATURE_WEIGHTS). Index groups:
#   0-12  MFCC 1..13 means    13-25  MFCC 1..13 std-devs    26-27  pitch
#
# IMPORTANT: these constants were measured on SIMULATED voices (see
# DEPLOYMENT.md, "Voice unlock"). They show that the method can separate
# voices, but real people vary more. Treat the thresholds below as a
# starting point and tune them with real recordings of your own users.
# Take-to-take wobble of each feature (x2 for real-world headroom).
FEATURE_SCALES = [
    1.5901, 0.9437, 0.3882, 0.4281, 0.4188, 0.2994, 0.2424,
    0.196, 0.1615, 0.1477, 0.1307, 0.1203, 0.1469, 1.2603,
    0.3625, 0.3704, 0.2614, 0.1834, 0.1628, 0.1684, 0.104,
    0.0977, 0.1234, 0.0864, 0.1022, 0.1314, 0.0616, 0.0583
]

# How much say each feature gets (sums to 1).
FEATURE_WEIGHTS = [
    0, 0.01328, 0.02509, 0.09828, 0.03306, 0.13624, 0.02189,
    0.01772, 0.04199, 0.01099, 0.00633, 0.04183, 0.05682, 0,
    0.00079, 0.01175, 0.04058, 0.06874, 0.10121, 0.07311, 0.03626,
    0, 0, 0, 0.01128, 0, 0.13342, 0.01933
]

# Five recordings give a much more reliable personal threshold than three
# (measured on simulated voices: false rejects fell from about 10% to 4%).
MIN_ENROLL_SAMPLES = 5
MAX_ENROLL_SAMPLES = 5
MIN_VOICED_MS = 700

# Distance thresholds, in the units described at distance().
#
# On simulated voices (5 enrollment recordings, held-out speakers) this
# setting rejected about 3% of genuine attempts and accepted about 2% of
# other voices. Real voices vary more than simulated ones, so expect
# more rejections, and check the numbers with real recordings.
# - Lower the numbers to be stricter (more rejections, fewer wrong accepts).
# - Raise them to be more lenient.
ENROLL_MAX_SPREAD = 1.6        # the 5 enrollment recordings must agree this well
THRESHOLD_FLOOR = 0.65         # strictest allowed acceptance distance
THRESHOLD_CEILING = 0.90       # most lenient allowed acceptance distance
THRESHOLD_MARGIN = 1.4         # acceptance = enrollment spread x this margin

STEP_UP_SECONDS = 600

MAX_VERIFY_ATTEMPTS = 5
VERIFY_WINDOW_SECONDS = 600


# =========================================
# MATCHING MATH (pure functions, no Flask)
# =========================================

def parse_features(raw):
    """Return a clean list of 28 floats, or None if the input is bad."""

    if not isinstance(raw, list) or len(raw) != FEATURE_COUNT:
        return None

    cleaned = []

    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None

        if not math.isfinite(value) or abs(value) > 1000:
            return None

        cleaned.append(float(value))

    return cleaned


def distance(first, second):
    """
    Weighted distance between two feature lists, in units of normal
    take-to-take variation. Around 1 means "about as different as two
    recordings of the same person"; clearly larger means a different voice.
    """

    total = 0.0

    for a, b, scale, weight in zip(
        first, second, FEATURE_SCALES, FEATURE_WEIGHTS
    ):
        total += weight * ((a - b) / scale) ** 2

    return math.sqrt(total)


def _mean_vector(vectors):
    count = len(vectors)

    return [
        sum(vector[index] for vector in vectors) / count
        for index in range(FEATURE_COUNT)
    ]


def build_template(samples):
    """
    Turn several enrollment recordings into a stored template.

    Raises ValueError (with a message safe to show the person) when
    the recordings are too different from each other to trust.
    """

    if not MIN_ENROLL_SAMPLES <= len(samples) <= MAX_ENROLL_SAMPLES:
        raise ValueError(
            f"Please record your passphrase {MIN_ENROLL_SAMPLES} times."
        )

    # Leave-one-out: how far is each recording from the average of
    # the others? This is a more honest spread than comparing each
    # recording with an average that already contains it.
    spreads = []

    for index, sample in enumerate(samples):
        others = [s for j, s in enumerate(samples) if j != index]
        spreads.append(distance(sample, _mean_vector(others)))

    spread = max(spreads)

    if spread > ENROLL_MAX_SPREAD:
        raise ValueError(
            "Your recordings sounded too different from each other. "
            "Please find a quiet place and say the same phrase, at the "
            "same distance from the microphone, each time."
        )

    threshold = min(
        max(spread * THRESHOLD_MARGIN, THRESHOLD_FLOOR),
        THRESHOLD_CEILING
    )

    return {
        "version": 1,
        "mean": _mean_vector(samples),
        "threshold": threshold,
        "samples": len(samples),
    }


def matches(template, features):
    """True when the recording is close enough to the stored template."""

    try:
        return distance(features, template["mean"]) <= template["threshold"]
    except (KeyError, TypeError, ValueError):
        return False


# =========================================
# STEP-UP GATE
# =========================================

def step_up_is_valid():
    until = session.get("step_up_until")

    return (
        isinstance(until, (int, float))
        and not isinstance(until, bool)
        and until > time.time()
    )


def grant_step_up():
    session["step_up_until"] = time.time() + STEP_UP_SECONDS


def require_step_up():
    """
    Call at the top of a sensitive view.

    Returns None when the person may continue (voice unlock is not set
    up, or they passed it recently). Otherwise returns a redirect to
    the unlock page.
    """

    user = current_user()

    if user is None:
        return None

    if database.get_voice_profile(user["id"]) is None:
        return None

    if step_up_is_valid():
        return None

    return redirect(
        url_for("voice.unlock", next=request.full_path.rstrip("?"))
    )


def _rate_key(kind, user_id):
    return f"{kind}:{user_id}"


def _client_ip():
    return request.remote_addr or "unknown"


# =========================================
# PAGES
# =========================================

@voice_bp.route("/unlock")
@login_required
def unlock():
    user = current_user()
    target = request.args.get("next", "")

    if not is_safe_redirect_target(target):
        target = url_for("capsules.index")

    has_profile = database.get_voice_profile(user["id"]) is not None

    if not has_profile or step_up_is_valid():
        return redirect(target)

    return render_template(
        "unlock.html",
        user=user,
        next_url=target
    )


@voice_bp.route("/unlock/password", methods=["POST"])
@login_required
def unlock_with_password():
    """The always-available fallback: confirm with the password."""

    user = current_user()
    target = request.form.get("next", "")

    if not is_safe_redirect_target(target):
        target = url_for("capsules.index")

    key = _rate_key("stepup", user["id"])

    if database.is_login_rate_limited(
        key,
        _client_ip(),
        max_attempts=MAX_VERIFY_ATTEMPTS,
        window_seconds=VERIFY_WINDOW_SECONDS,
        max_ip_attempts=30
    ):
        flash(
            "Too many attempts. Please wait a few minutes and try again.",
            "error"
        )
        return redirect(url_for("voice.unlock", next=target))

    password = request.form.get("password", "")
    stored_hash = database.get_password_hash_by_id(user["id"])

    if stored_hash and check_password_hash(stored_hash, password):
        database.clear_login_attempts(key, _client_ip())
        grant_step_up()
        return redirect(target)

    database.record_failed_login(key, _client_ip())
    flash("That password is not correct.", "error")

    return redirect(url_for("voice.unlock", next=target))


# =========================================
# JSON API
# =========================================

def _password_ok(user, password):
    if not isinstance(password, str) or not password:
        return False

    stored_hash = database.get_password_hash_by_id(user["id"])

    return bool(stored_hash) and check_password_hash(stored_hash, password)


@voice_bp.route("/api/voice/enroll", methods=["POST"])
@login_required
def enroll():
    user = current_user()
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "Invalid request."}), 400

    key = _rate_key("voiceenroll", user["id"])

    if database.is_login_rate_limited(
        key,
        _client_ip(),
        max_attempts=MAX_VERIFY_ATTEMPTS,
        window_seconds=VERIFY_WINDOW_SECONDS,
        max_ip_attempts=30
    ):
        return jsonify({
            "ok": False,
            "message": "Too many attempts. Please wait a few minutes."
        }), 429

    if not _password_ok(user, payload.get("password")):
        database.record_failed_login(key, _client_ip())
        return jsonify({
            "ok": False,
            "message": "Your password is not correct."
        }), 403

    raw_samples = payload.get("samples")

    if (
        not isinstance(raw_samples, list)
        or not MIN_ENROLL_SAMPLES <= len(raw_samples) <= MAX_ENROLL_SAMPLES
    ):
        return jsonify({
            "ok": False,
            "message": f"Please record your passphrase "
                       f"{MIN_ENROLL_SAMPLES} times."
        }), 400

    samples = []

    for item in raw_samples:
        if not isinstance(item, dict):
            return jsonify({
                "ok": False,
                "message": "Invalid recording."
            }), 400

        features = parse_features(item.get("features"))
        voiced_ms = item.get("voiced_ms")

        if features is None or isinstance(voiced_ms, bool) or not isinstance(
            voiced_ms, (int, float)
        ):
            return jsonify({
                "ok": False,
                "message": "Invalid recording."
            }), 400

        if voiced_ms < MIN_VOICED_MS:
            return jsonify({
                "ok": False,
                "message": "A recording was too quiet or too short. "
                           "Please speak for at least a second."
            }), 400

        samples.append(features)

    try:
        template = build_template(samples)
    except ValueError as error:
        return jsonify({"ok": False, "message": str(error)}), 422

    database.save_voice_profile(
        user["id"],
        json.dumps(template),
        len(samples)
    )

    database.clear_login_attempts(key, _client_ip())
    grant_step_up()

    return jsonify({
        "ok": True,
        "message": "Voice unlock is ready."
    })


@voice_bp.route("/api/voice/verify", methods=["POST"])
@login_required
def verify():
    user = current_user()
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "Invalid request."}), 400

    key = _rate_key("voice", user["id"])

    if database.is_login_rate_limited(
        key,
        _client_ip(),
        max_attempts=MAX_VERIFY_ATTEMPTS,
        window_seconds=VERIFY_WINDOW_SECONDS,
        max_ip_attempts=30
    ):
        return jsonify({
            "ok": False,
            "locked": True,
            "message": "Too many tries. Please use your password instead."
        }), 429

    profile = database.get_voice_profile(user["id"])

    if profile is None:
        return jsonify({
            "ok": False,
            "message": "Voice unlock is not set up."
        }), 400

    features = parse_features(payload.get("features"))
    voiced_ms = payload.get("voiced_ms")

    if features is None or isinstance(voiced_ms, bool) or not isinstance(
        voiced_ms, (int, float)
    ):
        return jsonify({"ok": False, "message": "Invalid recording."}), 400

    if voiced_ms < MIN_VOICED_MS:
        return jsonify({
            "ok": False,
            "message": "I could not hear enough. Please speak for at "
                       "least a second."
        })

    try:
        template = json.loads(profile["template"])
    except (TypeError, ValueError):
        current_app.logger.error("Stored voice template is unreadable.")
        return jsonify({
            "ok": False,
            "message": "Please use your password instead."
        }), 500

    if matches(template, features):
        database.clear_login_attempts(key, _client_ip())
        grant_step_up()
        return jsonify({"ok": True, "message": "Voice recognized."})

    database.record_failed_login(key, _client_ip())

    # No score is returned, so nobody can use the response to tune a
    # recording towards the template.
    return jsonify({
        "ok": False,
        "message": "That did not sound like the voice I know. "
                   "Try again, or use your password."
    })


@voice_bp.route("/api/voice/delete", methods=["POST"])
@login_required
def delete():
    user = current_user()
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "Invalid request."}), 400

    key = _rate_key("voicedelete", user["id"])

    if database.is_login_rate_limited(
        key,
        _client_ip(),
        max_attempts=MAX_VERIFY_ATTEMPTS,
        window_seconds=VERIFY_WINDOW_SECONDS,
        max_ip_attempts=30
    ):
        return jsonify({
            "ok": False,
            "message": "Too many attempts. Please wait a few minutes."
        }), 429

    if not _password_ok(user, payload.get("password")):
        database.record_failed_login(key, _client_ip())
        return jsonify({
            "ok": False,
            "message": "Your password is not correct."
        }), 403

    database.delete_voice_profile(user["id"])
    database.clear_login_attempts(key, _client_ip())
    session.pop("step_up_until", None)

    return jsonify({"ok": True, "message": "Voice unlock was removed."})
