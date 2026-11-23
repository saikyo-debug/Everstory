"""
Everstory - photo, voice and video attachments.

Security rules this module follows:

* The file TYPE is decided from the file's own first bytes ("magic
  numbers"), never from the file name or the type the browser claims.
  SVG and HTML are not accepted at all.
* Files are stored under random names in a folder that is NOT the
  public static folder, so nobody can guess or browse to them.
* Files are only served to the signed-in owner, through /media/<id>,
  and files that belong to a sealed time capsule are not served until
  the capsule unlocks.
* Size limits are enforced while the file is being written, so an
  oversized upload is stopped early and the partial file is removed.
"""

import re
import secrets
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    request,
    send_file,
    url_for
)

from werkzeug.utils import secure_filename

import config
import database
from auth_utils import (
    current_user,
    is_safe_redirect_target,
    login_required
)


media_bp = Blueprint("media", __name__)

STORED_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")

SIZE_LIMITS = {
    "photo": config.MAX_PHOTO_BYTES,
    "audio": config.MAX_AUDIO_BYTES,
    "video": config.MAX_VIDEO_BYTES,
}

CHUNK_SIZE = 64 * 1024


def _human_size(number_of_bytes):
    megabytes = number_of_bytes / (1024 * 1024)

    if megabytes >= 1:
        return f"{megabytes:.0f} MB"

    return f"{max(1, number_of_bytes // 1024)} KB"


def detect_media_type(header, declared_mime=""):
    """
    Identify a file from its first bytes.

    Returns (kind, mime_type, extension) or None when the file is not
    a supported photo, audio or video format.

    Some containers (MP4, WebM, Ogg) can hold either audio or video.
    For those the browser's declared type only chooses between the two
    safe options; it can never make an unsupported file acceptable.
    """

    declared = (declared_mime or "").lower()
    declared_audio = declared.startswith("audio/")

    if header.startswith(b"\xff\xd8\xff"):
        return "photo", "image/jpeg", ".jpg"

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "photo", "image/png", ".png"

    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "photo", "image/gif", ".gif"

    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "photo", "image/webp", ".webp"

    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio", "audio/wav", ".wav"

    if header[:4] == b"OggS":
        if b"\x80theora" in header:
            return "video", "video/ogg", ".ogv"

        return "audio", "audio/ogg", ".ogg"

    if header[:3] == b"ID3" or (
        len(header) > 1
        and header[0] == 0xFF
        and (header[1] & 0xE0) == 0xE0
        and (header[1] & 0x18) != 0x08
    ):
        return "audio", "audio/mpeg", ".mp3"

    if header[4:8] == b"ftyp":
        brand = header[8:12]

        if brand == b"qt  ":
            return "video", "video/quicktime", ".mov"

        if brand in (b"M4A ", b"M4B ", b"M4P ") or declared_audio:
            return "audio", "audio/mp4", ".m4a"

        return "video", "video/mp4", ".mp4"

    if header.startswith(b"\x1a\x45\xdf\xa3"):
        # WebM/Matroska names its tracks near the start of the file. A
        # video codec means video; only audio codecs mean a voice note.
        # (Browsers label every ".webm" as video, so the declared type
        # cannot be trusted for this.)
        has_video = any(
            tag in header
            for tag in (b"V_VP8", b"V_VP9", b"V_AV1", b"V_MPEG4", b"V_MS/VFW")
        )
        has_audio = b"A_OPUS" in header or b"A_VORBIS" in header

        if has_video:
            return "video", "video/webm", ".webm"

        if has_audio or declared_audio:
            return "audio", "audio/webm", ".webm"

        return "video", "video/webm", ".webm"

    return None


def upload_root():
    root = Path(current_app.config["UPLOAD_FOLDER"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def stored_file_path(user_id, stored_name):
    """Return the on-disk path of an attachment, or None if unsafe."""

    if not STORED_NAME_PATTERN.match(stored_name or ""):
        return None

    return upload_root() / str(int(user_id)) / stored_name


def remove_stored_files(user_id, stored_names):
    """Delete attachment files from disk. Missing files are ignored."""

    for stored_name in stored_names or []:
        path = stored_file_path(user_id, stored_name)

        if path is None:
            continue

        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            current_app.logger.warning(
                "Could not remove attachment file %s", stored_name
            )


def save_uploaded_files(
    file_storages,
    user_id,
    entry_id=None,
    capsule_id=None
):
    """
    Validate and store uploaded attachments.

    Returns (saved_count, error_messages). Files that fail validation
    are skipped with a friendly message; the others are still saved.
    """

    if (entry_id is None) == (capsule_id is None):
        raise ValueError("Provide exactly one of entry_id or capsule_id.")

    if entry_id is not None:
        existing = database.count_media_for_entry(entry_id, user_id)
    else:
        existing = database.count_media_for_capsule(capsule_id, user_id)

    user_folder = upload_root() / str(int(user_id))
    user_folder.mkdir(parents=True, exist_ok=True)

    saved = 0
    errors = []

    for storage in file_storages:

        # An untouched file input still submits one empty part.
        if storage is None or not storage.filename:
            continue

        display_name = (
            secure_filename(storage.filename) or "attachment"
        )[:120]

        if existing + saved >= config.MAX_MEDIA_PER_ITEM:
            errors.append(
                f"You can attach up to {config.MAX_MEDIA_PER_ITEM} "
                f"files. \"{display_name}\" was not added."
            )
            continue

        header = storage.stream.read(8192)
        storage.stream.seek(0)

        detected = detect_media_type(header, storage.mimetype)

        if detected is None:
            errors.append(
                f"\"{display_name}\" is not a supported photo, voice "
                "or video file."
            )
            continue

        kind, mime_type, extension = detected
        size_limit = SIZE_LIMITS[kind]

        stored_name = secrets.token_hex(16) + extension
        destination = user_folder / stored_name

        written = 0
        too_large = False

        try:
            with open(destination, "wb") as output:

                while True:
                    chunk = storage.stream.read(CHUNK_SIZE)

                    if not chunk:
                        break

                    written += len(chunk)

                    if written > size_limit:
                        too_large = True
                        break

                    output.write(chunk)

        except OSError:
            current_app.logger.exception(
                "Could not write an uploaded attachment."
            )
            destination.unlink(missing_ok=True)
            errors.append(
                f"\"{display_name}\" could not be saved. "
                "Please try again."
            )
            continue

        if too_large:
            destination.unlink(missing_ok=True)
            errors.append(
                f"\"{display_name}\" is larger than the "
                f"{_human_size(size_limit)} limit for {kind} files."
            )
            continue

        if written == 0:
            destination.unlink(missing_ok=True)
            errors.append(f"\"{display_name}\" is empty.")
            continue

        try:
            database.create_media(
                user_id=user_id,
                kind=kind,
                original_name=display_name,
                stored_name=stored_name,
                mime_type=mime_type,
                size_bytes=written,
                entry_id=entry_id,
                capsule_id=capsule_id
            )
        except Exception:
            current_app.logger.exception(
                "Could not record an uploaded attachment."
            )
            destination.unlink(missing_ok=True)
            errors.append(
                f"\"{display_name}\" could not be saved. "
                "Please try again."
            )
            continue

        saved += 1

    return saved, errors


def flash_upload_result(saved, errors):
    """Tell the user what happened to their attachments."""

    for message in errors:
        flash(message, "error")

    return saved


# =========================================
# ROUTES
# =========================================

@media_bp.route("/media/<int:media_id>")
@login_required
def serve_media(media_id):
    """Send an attachment to its owner (never to anyone else)."""

    user = current_user()
    media = database.get_media_by_id(media_id)

    if media is None or media["user_id"] != user["id"]:
        abort(404)

    # Attachments of a sealed time capsule stay locked until it opens.
    if media["capsule_id"] is not None:
        capsule = database.get_capsule_content(
            media["capsule_id"],
            user["id"]
        )

        if capsule is None:
            abort(404)

    path = stored_file_path(user["id"], media["stored_name"])

    if path is None or not path.is_file():
        abort(404)

    response = send_file(
        path,
        mimetype=media["mime_type"],
        as_attachment=False,
        download_name=media["original_name"],
        conditional=True,
        max_age=0
    )

    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"

    # If anyone ever opens an attachment directly in a tab, the
    # browser must not run anything inside it.
    response.headers["Content-Security-Policy"] = (
        "sandbox; default-src 'none'; "
        "img-src 'self'; media-src 'self'; style-src 'unsafe-inline'"
    )

    return response


@media_bp.route("/entries/<int:entry_id>/media", methods=["POST"])
@login_required
def add_entry_media(entry_id):
    """Attach more photos, voice notes or videos to a diary entry."""

    user = current_user()
    entry = database.get_entry_by_id_and_user(entry_id, user["id"])

    if entry is None:
        abort(404)

    files = request.files.getlist("media")

    if not any(storage and storage.filename for storage in files):
        flash("Choose a photo, voice note or video first.", "error")
        return redirect(url_for("edit_entry", entry_id=entry_id))

    saved, errors = save_uploaded_files(
        files,
        user["id"],
        entry_id=entry_id
    )

    flash_upload_result(saved, errors)

    if saved:
        flash(
            "Attachment added to your memory."
            if saved == 1
            else f"{saved} attachments added to your memory.",
            "success"
        )

    return redirect(url_for("edit_entry", entry_id=entry_id))


@media_bp.route("/media/<int:media_id>/delete", methods=["POST"])
@login_required
def delete_media(media_id):
    """Remove one attachment."""

    user = current_user()
    row = database.delete_media_row(media_id, user["id"])

    if row is None:
        abort(404)

    remove_stored_files(user["id"], [row["stored_name"]])

    flash("Attachment removed.", "success")

    target = request.form.get("next", "")

    if is_safe_redirect_target(target):
        return redirect(target)

    if row["entry_id"] is not None:
        return redirect(url_for("view_entry", entry_id=row["entry_id"]))

    return redirect(url_for("capsules.index"))
