"""
Everstory - shared authentication helpers.

One `login_required` decorator replaces the same "is the user logged
in?" block that used to be copied into every route, and every page now
reaches the signed-in user in exactly the same way.
"""

from functools import wraps
from urllib.parse import urlsplit

from flask import (
    flash,
    g,
    jsonify,
    redirect,
    request,
    session,
    url_for
)

import database


def current_user():
    """
    Return the signed-in user row, or None.

    The row is loaded once per request and remembered on `g`, so the
    session check, the templates and the view all share one query.
    """

    if "current_user" in g:
        return g.current_user

    user = None
    user_id = session.get("user_id")

    if user_id is not None:
        user = database.get_user_by_id(user_id)

    g.current_user = user
    return user


def is_safe_redirect_target(target):
    """
    Allow only local paths such as "/entries?page=2".

    Anything with a scheme or host ("https://evil.example", "//evil",
    "/\\evil") is rejected so the login page cannot be abused as an
    open redirect.
    """

    if not isinstance(target, str) or not target:
        return False

    if not target.startswith("/") or target.startswith("//"):
        return False

    if "\\" in target or "\r" in target or "\n" in target:
        return False

    parts = urlsplit(target)

    return not parts.scheme and not parts.netloc


def login_required(view):
    """
    Protect a route.

    Browser pages are redirected to the login page (remembering where
    the visitor was heading). JSON API routes answer 401 instead, so
    JavaScript can react without being handed an HTML page.
    """

    @wraps(view)
    def wrapped_view(*args, **kwargs):

        user = current_user()

        if user is None:

            if "user_id" in session:
                session.clear()

            if request.path.startswith("/api/"):
                return jsonify({
                    "error": "Authentication required."
                }), 401

            flash("Please log in to continue.", "error")

            if request.method == "GET":
                return redirect(
                    url_for("login", next=request.full_path.rstrip("?"))
                )

            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped_view
