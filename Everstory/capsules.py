"""
Everstory - Digital Time Capsule.

A time capsule is a message (with optional photos, voice notes or
videos) written now and locked until a future date.

How "sealed" is enforced:

* The list page and the sealed page only ever load the capsule's
  HEADER (title and dates). The message text is not even read from
  the database until the unlock time has passed - the SQL itself has an
  `unlock_at <= now` condition.
* Attachments of a sealed capsule are refused by the media route.
* Because the server decides, changing the page in the browser (or
  editing the URL) cannot reveal a sealed capsule.

Times are Philippine time, like the rest of the diary.
"""

from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for
)

import config
import database
from auth_utils import current_user, login_required
from media import (
    flash_upload_result,
    remove_stored_files,
    save_uploaded_files
)
from voiceprint import require_step_up


capsules_bp = Blueprint("capsules", __name__, url_prefix="/capsules")

MIN_LEAD_TIME = timedelta(minutes=1)
MAX_LEAD_TIME = timedelta(days=365 * 50)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _now():
    """Current Philippine time as a naive datetime."""

    return database.get_local_time().replace(tzinfo=None)


def parse_unlock_datetime(date_text, time_text):
    """Combine the form's date and time fields, or return None."""

    try:
        day = datetime.strptime((date_text or "").strip(), "%Y-%m-%d")
        clock = datetime.strptime(
            (time_text or "").strip() or "09:00",
            "%H:%M"
        )
    except ValueError:
        return None

    return datetime.combine(day.date(), clock.time())


def describe_capsule(row, now=None):
    """
    Add display information to a capsule row.

    state:  "sealed"  - still locked
            "ready"   - unlocked but never opened
            "opened"  - unlocked and opened before
    """

    now = now or _now()

    unlock_at = datetime.strptime(row["unlock_at"], TIMESTAMP_FORMAT)
    remaining = unlock_at - now

    if remaining.total_seconds() > 0:
        state = "sealed"
    elif row["opened_at"]:
        state = "opened"
    else:
        state = "ready"

    if state == "sealed":
        total_minutes = int(remaining.total_seconds() // 60)
        days = total_minutes // (60 * 24)

        if days >= 2:
            countdown = f"Opens in {days} days"
        elif days == 1:
            countdown = "Opens in 1 day"
        elif total_minutes >= 60:
            countdown = f"Opens in {total_minutes // 60} hours"
        else:
            countdown = f"Opens in {max(1, total_minutes)} minutes"
    elif state == "ready":
        countdown = "Ready to open"
    else:
        countdown = "Opened"

    return {
        "id": row["id"],
        "title": row["title"],
        "state": state,
        "countdown": countdown,
        "created_at": row["created_at"],
        "unlock_at": row["unlock_at"],
        "unlock_label": unlock_at.strftime("%B %d, %Y at %I:%M %p")
        .replace(" 0", " "),
        "opened_at": row["opened_at"],
    }


@capsules_bp.route("")
@login_required
def index():
    user = current_user()
    now = _now()

    capsules = [
        describe_capsule(row, now)
        for row in database.list_capsules(user["id"])
    ]

    return render_template(
        "capsules.html",
        user=user,
        ready=[c for c in capsules if c["state"] == "ready"],
        sealed=[c for c in capsules if c["state"] == "sealed"],
        opened=[c for c in capsules if c["state"] == "opened"],
        total=len(capsules)
    )


@capsules_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    user = current_user()
    now = _now()

    def render_form(status=200):
        return render_template(
            "capsule_form.html",
            user=user,
            min_date=(now + timedelta(days=0)).strftime("%Y-%m-%d"),
            max_title=config.MAX_TITLE_LENGTH,
            max_files=config.MAX_MEDIA_PER_ITEM
        ), status

    if request.method == "GET":
        return render_form()

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("Please add a title and a message for your capsule.", "error")
        return render_form(400)

    if len(title) > config.MAX_TITLE_LENGTH:
        flash(
            f"Your title must not exceed {config.MAX_TITLE_LENGTH} "
            "characters.",
            "error"
        )
        return render_form(400)

    if len(content) > config.MAX_ENTRY_LENGTH:
        flash("Your message is too long for one capsule.", "error")
        return render_form(400)

    unlock = parse_unlock_datetime(
        request.form.get("unlock_date"),
        request.form.get("unlock_time")
    )

    if unlock is None:
        flash("Please choose when the capsule should open.", "error")
        return render_form(400)

    if unlock < now + MIN_LEAD_TIME:
        flash("The opening date must be in the future.", "error")
        return render_form(400)

    if unlock > now + MAX_LEAD_TIME:
        flash("Please choose a date within the next 50 years.", "error")
        return render_form(400)

    try:
        capsule_id = database.create_capsule(
            user["id"],
            title,
            content,
            unlock.strftime(TIMESTAMP_FORMAT)
        )
    except Exception:
        from flask import current_app
        current_app.logger.exception("Time capsule creation failed.")
        flash("We couldn't seal your capsule. Please try again.", "error")
        return render_form(500)

    files = request.files.getlist("media")

    if any(storage and storage.filename for storage in files):
        saved, errors = save_uploaded_files(
            files,
            user["id"],
            capsule_id=capsule_id
        )
        flash_upload_result(saved, errors)

    flash(
        "Your time capsule is sealed. It will open on "
        f"{unlock.strftime('%B %d, %Y')}. 💜",
        "success"
    )

    return redirect(url_for("capsules.index"))


@capsules_bp.route("/<int:capsule_id>")
@login_required
def view(capsule_id):
    user = current_user()

    header = database.get_capsule_header(capsule_id, user["id"])

    if header is None:
        abort(404)

    info = describe_capsule(header)

    # Sealed: show the countdown only. The message is never loaded.
    if info["state"] == "sealed":
        return render_template(
            "capsule_view.html",
            user=user,
            capsule=info,
            content=None,
            media=[]
        )

    # Opening a capsule is a sensitive action: when voice unlock is
    # set up, the person must pass it first.
    blocked = require_step_up()

    if blocked is not None:
        return blocked

    capsule = database.get_capsule_content(capsule_id, user["id"])

    if capsule is None:
        abort(404)

    database.mark_capsule_opened(capsule_id, user["id"])

    media = database.list_media_for_capsule(capsule_id, user["id"])

    return render_template(
        "capsule_view.html",
        user=user,
        capsule=describe_capsule(
            database.get_capsule_header(capsule_id, user["id"])
        ),
        content=capsule["content"],
        media=media
    )


@capsules_bp.route("/<int:capsule_id>/delete", methods=["POST"])
@login_required
def delete(capsule_id):
    user = current_user()

    stored_names = database.delete_capsule_returning_media(
        capsule_id,
        user["id"]
    )

    if stored_names is None:
        abort(404)

    remove_stored_files(user["id"], stored_names)

    flash("Your time capsule has been deleted.", "success")

    return redirect(url_for("capsules.index"))
