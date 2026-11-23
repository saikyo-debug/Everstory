"""
Everstory - JSON API used for offline use and syncing.

While offline, the browser keeps new memories in a queue on the
device. When the connection returns it sends each one here. Every
queued memory carries a random `client_id`, so if a sync is cut off and
repeated, the memory is stored once - never twice.

Both routes require a signed-in session and the CSRF header, like
every other state-changing route.
"""

import re
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

import config
import database
from auth_utils import current_user, login_required


sync_bp = Blueprint("sync", __name__)

CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

MAX_CACHED_ENTRIES = 2000

# An offline memory may be older than "now", but not unreasonably so,
# and never from the future.
MAX_OFFLINE_AGE = timedelta(days=60)
FUTURE_TOLERANCE = timedelta(minutes=5)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def entry_to_json(entry):
    return {
        "id": entry["id"],
        "title": entry["title"],
        "content": entry["content"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "is_favorite": bool(entry["is_favorite"]),
    }


def choose_created_at(client_value):
    """
    Use the time the memory was written offline when it is believable,
    otherwise fall back to "now".
    """

    now = database.get_local_time().replace(tzinfo=None)
    converted = database.utc_iso_to_local_timestamp(client_value)

    if converted is None:
        return now.strftime(TIMESTAMP_FORMAT)

    try:
        written_at = datetime.strptime(converted, TIMESTAMP_FORMAT)
    except ValueError:
        return now.strftime(TIMESTAMP_FORMAT)

    if written_at > now + FUTURE_TOLERANCE:
        return now.strftime(TIMESTAMP_FORMAT)

    if written_at < now - MAX_OFFLINE_AGE:
        return now.strftime(TIMESTAMP_FORMAT)

    return min(written_at, now).strftime(TIMESTAMP_FORMAT)


@sync_bp.route("/api/entries", methods=["GET"])
@login_required
def list_entries():
    """All of this person's memories, for the offline copy."""

    user = current_user()

    entries, total = database.get_entries_page(
        user["id"],
        page=1,
        per_page=MAX_CACHED_ENTRIES
    )

    response = jsonify({
        "user_id": user["id"],
        "total": total,
        "entries": [entry_to_json(entry) for entry in entries],
    })

    response.headers["Cache-Control"] = "no-store"

    return response


@sync_bp.route("/api/entries", methods=["POST"])
@login_required
def create_entry_from_offline():
    user = current_user()
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Send a JSON memory."}), 400

    client_id = payload.get("client_id")
    title = payload.get("title")
    content = payload.get("content")

    if not isinstance(client_id, str) or not CLIENT_ID_PATTERN.match(
        client_id
    ):
        return jsonify({"ok": False, "error": "Invalid client id."}), 400

    if not isinstance(title, str) or not isinstance(content, str):
        return jsonify({
            "ok": False,
            "error": "A title and a memory are required."
        }), 400

    title = title.strip()
    content = content.strip()

    if not title or not content:
        return jsonify({
            "ok": False,
            "error": "A title and a memory are required."
        }), 400

    if len(title) > config.MAX_TITLE_LENGTH:
        return jsonify({"ok": False, "error": "The title is too long."}), 400

    if len(content) > config.MAX_ENTRY_LENGTH:
        return jsonify({"ok": False, "error": "The memory is too long."}), 400

    # Syncing the same memory twice must not create a second copy.
    existing = database.get_entry_by_client_id(user["id"], client_id)

    if existing is not None:
        return jsonify({
            "ok": True,
            "duplicate": True,
            "entry": entry_to_json(existing)
        }), 200

    try:
        entry_id = database.create_entry(
            user["id"],
            title,
            content,
            created_at=choose_created_at(payload.get("created_at_utc")),
            client_id=client_id
        )
    except sqlite3.IntegrityError:
        # Another tab synced the same memory a moment earlier.
        existing = database.get_entry_by_client_id(user["id"], client_id)

        if existing is None:
            raise

        return jsonify({
            "ok": True,
            "duplicate": True,
            "entry": entry_to_json(existing)
        }), 200

    entry = database.get_entry_by_id_and_user(entry_id, user["id"])

    return jsonify({
        "ok": True,
        "duplicate": False,
        "entry": entry_to_json(entry)
    }), 201
