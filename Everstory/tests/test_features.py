"""
Everstory - tests for the features added on top of the original app:
memory view/edit/delete/favorites/search, photo-voice-video attachments,
the diary assistant, time capsules, offline sync API, voice unlock,
the database upgrade, and the security rules around them.

Each test run uses its own temporary database and upload folder.
Your real alaala.db is never touched.
"""

import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import uuid

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if not (
    os.environ.get("EVERSTORY_SECRET_KEY")
    or os.environ.get("ALAALA_SECRET_KEY")
):
    os.environ["EVERSTORY_SECRET_KEY"] = (
        "test-only-secret-key-never-use-in-production-0123456789"
    )

import database

_previous_database_path = database.DATABASE_PATH
_temp_database_path = (
    Path(tempfile.gettempdir()) / f"everstory_features_{uuid.uuid4().hex}.db"
)

database.DATABASE_PATH = _temp_database_path
database.initialize_database()

import app as app_module
import assistant
import media
import voiceprint

flask_app = app_module.app


def setUpModule():
    # Other test modules also point the app at their own temporary
    # database; this module uses (and later gives back) its own.
    database.DATABASE_PATH = _temp_database_path
    database.initialize_database()
    flask_app.config["UPLOAD_FOLDER"] = tempfile.mkdtemp(prefix="everstory_up_")


def tearDownModule():
    database.DATABASE_PATH = _previous_database_path

    for suffix in ("", "-wal", "-shm"):
        Path(str(_temp_database_path) + suffix).unlink(missing_ok=True)


# Tiny but valid files (the first bytes decide the type).
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
    b"\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)
WEBM_AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 40 + b"A_OPUS" + b"\x00" * 100
WEBM_VIDEO = b"\x1a\x45\xdf\xa3" + b"\x00" * 40 + b"V_VP9" + b"\x00" * 100
MP4_VIDEO = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 100


def csrf_from(client, path):
    page = client.get(path).get_data(as_text=True)
    match = (
        re.search(r'name="csrf_token" value="([^"]+)"', page)
        or re.search(r'name="csrf-token" content="([^"]+)"', page)
    )
    assert match, f"No CSRF token found on {path}"
    return match.group(1)


class FeatureTestCase(unittest.TestCase):

    def setUp(self):
        database.DATABASE_PATH = _temp_database_path
        self.client = flask_app.test_client()
        self.email = f"{uuid.uuid4().hex[:10]}@example.com"
        self.user_id = self.register_and_login(self.client, self.email)

    def register_and_login(self, client, email, name="Test Person"):
        token = csrf_from(client, "/register")
        client.post("/register", data={
            "csrf_token": token, "full_name": name, "email": email,
            "password": "password123", "confirm_password": "password123"
        })
        token = csrf_from(client, "/login")
        response = client.post("/login", data={
            "csrf_token": token, "email": email, "password": "password123"
        })
        self.assertEqual(response.status_code, 302)
        return database.get_user_by_email(email)["id"]

    def post(self, path, client=None, **fields):
        client = client or self.client
        token = csrf_from(client, "/settings")
        fields["csrf_token"] = token
        return client.post(path, data=fields)

    def other_client(self):
        client = flask_app.test_client()
        self.register_and_login(client, f"{uuid.uuid4().hex[:10]}@example.com")
        return client

    def make_entry(self, title="A memory", content="Some text.", **kw):
        return database.create_entry(self.user_id, title, content, **kw)


# ============================================================
class MemoryManagementTests(FeatureTestCase):

    def test_view_edit_favorite_delete_cycle(self):
        entry_id = self.make_entry("Beach day", "We swam all afternoon.")

        page = self.client.get(f"/entries/{entry_id}").get_data(as_text=True)
        self.assertIn("Beach day", page)
        self.assertIn("We swam all afternoon.", page)

        response = self.post(
            f"/entries/{entry_id}/edit", title="Beach day!", content="Updated."
        )
        self.assertEqual(response.status_code, 302)
        entry = database.get_entry_by_id_and_user(entry_id, self.user_id)
        self.assertEqual((entry["title"], entry["content"]), ("Beach day!", "Updated."))

        self.post(f"/entries/{entry_id}/favorite")
        self.assertEqual(
            database.get_entry_by_id_and_user(entry_id, self.user_id)["is_favorite"], 1
        )
        self.post(f"/entries/{entry_id}/favorite")
        self.assertEqual(
            database.get_entry_by_id_and_user(entry_id, self.user_id)["is_favorite"], 0
        )

        self.post(f"/entries/{entry_id}/delete")
        self.assertIsNone(database.get_entry_by_id_and_user(entry_id, self.user_id))

    def test_other_users_cannot_view_edit_delete_or_favorite(self):
        entry_id = self.make_entry("Private", "Mine only.")
        stranger = self.other_client()

        self.assertEqual(stranger.get(f"/entries/{entry_id}").status_code, 404)
        self.assertEqual(stranger.get(f"/entries/{entry_id}/edit").status_code, 404)

        for action in ("edit", "delete", "favorite"):
            response = self.post(
                f"/entries/{entry_id}/{action}", client=stranger,
                title="Hacked", content="Hacked"
            )
            self.assertEqual(response.status_code, 404, action)

        entry = database.get_entry_by_id_and_user(entry_id, self.user_id)
        self.assertEqual(entry["title"], "Private")

    def test_blank_or_oversized_edit_is_rejected(self):
        entry_id = self.make_entry("Keep me", "Original")
        self.post(f"/entries/{entry_id}/edit", title="  ", content="x")
        self.post(f"/entries/{entry_id}/edit", title="x" * 151, content="x")
        self.post(f"/entries/{entry_id}/edit", title="ok", content="x" * 50_001)
        entry = database.get_entry_by_id_and_user(entry_id, self.user_id)
        self.assertEqual(entry["title"], "Keep me")

    def test_search_filters_and_paginates(self):
        for i in range(12):
            self.make_entry(f"Entry {i}", f"filler {i}")
        self.make_entry("Needle", "the special word is lechon")

        rows, total = database.get_entries_page(self.user_id, query="lechon")
        self.assertEqual(total, 1)
        rows, total = database.get_entries_page(self.user_id, per_page=10)
        self.assertEqual((len(rows), total), (10, 13))
        rows, _ = database.get_entries_page(self.user_id, page=2, per_page=10)
        self.assertEqual(len(rows), 3)

    def test_search_treats_wildcards_literally(self):
        self.make_entry("Normal", "nothing special")
        _rows, total = database.get_entries_page(self.user_id, query="%")
        self.assertEqual(total, 0)
        _rows, total = database.get_entries_page(self.user_id, query="_")
        self.assertEqual(total, 0)

    def test_stats_are_real(self):
        first = self.make_entry("One", "a", created_at="2026-05-01 10:00:00")
        self.make_entry("Two", "b", created_at="2026-05-01 12:00:00")
        self.make_entry("Three", "c", created_at="2026-05-02 09:00:00")
        database.set_entry_favorite(first, self.user_id, True)
        self.assertEqual(
            database.get_entry_stats(self.user_id),
            {"total": 3, "favorites": 1, "days": 2}
        )

    def test_memory_text_is_escaped_not_executed(self):
        entry_id = self.make_entry("<b>x</b>", "<script>alert(1)</script>")
        page = self.client.get(f"/entries/{entry_id}").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)


# ============================================================
class MediaTests(FeatureTestCase):

    def upload(self, entry_id, name, data, client=None):
        client = client or self.client
        token = csrf_from(client, "/settings")
        return client.post(
            f"/entries/{entry_id}/media",
            data={"csrf_token": token, "media": (io.BytesIO(data), name)},
            content_type="multipart/form-data"
        )

    def test_type_detection_uses_file_content(self):
        self.assertEqual(media.detect_media_type(PNG_BYTES)[0], "photo")
        self.assertEqual(media.detect_media_type(b"\xff\xd8\xff\xe0")[0], "photo")
        self.assertEqual(media.detect_media_type(WEBM_AUDIO, "video/webm")[0], "audio")
        self.assertEqual(media.detect_media_type(WEBM_VIDEO, "audio/webm")[0], "video")
        self.assertEqual(media.detect_media_type(MP4_VIDEO, "video/mp4")[0], "video")
        self.assertIsNone(media.detect_media_type(b"<html><script>"))
        self.assertIsNone(media.detect_media_type(b"<svg xmlns='x'><script>"))
        self.assertIsNone(media.detect_media_type(b"MZ\x90\x00 an exe"))

    def test_valid_upload_is_stored_and_served_to_owner_only(self):
        entry_id = self.make_entry()
        self.upload(entry_id, "../../evil name.png", PNG_BYTES)

        rows = database.list_media_for_entry(entry_id, self.user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "photo")
        self.assertNotIn("..", rows[0]["original_name"])
        self.assertRegex(rows[0]["stored_name"], r"^[0-9a-f]{32}\.png$")

        response = self.client.get(f"/media/{rows[0]['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response.headers["Content-Security-Policy"])

        stranger = self.other_client()
        self.assertEqual(stranger.get(f"/media/{rows[0]['id']}").status_code, 404)
        self.assertIn(
            flask_app.test_client().get(f"/media/{rows[0]['id']}").status_code,
            (302, 401, 404)
        )

    def test_disguised_and_dangerous_files_are_rejected(self):
        entry_id = self.make_entry()
        self.upload(entry_id, "photo.png", b"<html><script>alert(1)</script>")
        self.upload(entry_id, "x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.upload(entry_id, "empty.png", b"")
        self.assertEqual(database.count_media_for_entry(entry_id, self.user_id), 0)

    def test_cannot_attach_to_someone_elses_memory(self):
        entry_id = self.make_entry()
        stranger = self.other_client()
        response = self.upload(entry_id, "a.png", PNG_BYTES, client=stranger)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(database.count_media_for_entry(entry_id, self.user_id), 0)

    def test_attachment_limit_per_memory(self):
        entry_id = self.make_entry()
        for _ in range(12):
            self.upload(entry_id, "a.png", PNG_BYTES)
        self.assertEqual(
            database.count_media_for_entry(entry_id, self.user_id), 10
        )

    def test_deleting_a_memory_deletes_its_files(self):
        entry_id = self.make_entry()
        self.upload(entry_id, "a.png", PNG_BYTES)
        row = database.list_media_for_entry(entry_id, self.user_id)[0]
        with flask_app.app_context():
            path = media.stored_file_path(self.user_id, row["stored_name"])

        self.assertTrue(path.is_file())

        self.post(f"/entries/{entry_id}/delete")
        self.assertFalse(path.exists())
        self.assertIsNone(database.get_media_by_id(row["id"]))

    def test_path_traversal_names_are_never_used(self):
        with flask_app.app_context():
            self.assertIsNone(media.stored_file_path(1, "../../etc/passwd"))
            self.assertIsNone(media.stored_file_path(1, "abc.png"))


# ============================================================
class CapsuleTests(FeatureTestCase):

    def future(self, days=30):
        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    def create_capsule(self, **overrides):
        data = {
            "title": "Letter", "content": "TOP-SECRET-TEXT",
            "unlock_date": self.future(), "unlock_time": "09:00"
        }
        data.update(overrides)
        return self.post("/capsules/new", **data)

    def test_sealed_capsule_never_reveals_its_message(self):
        self.create_capsule()
        capsule = database.list_capsules(self.user_id)[0]

        page = self.client.get(f"/capsules/{capsule['id']}").get_data(as_text=True)
        self.assertNotIn("TOP-SECRET-TEXT", page)
        self.assertIn("STILL SEALED", page)

        listing = self.client.get("/capsules").get_data(as_text=True)
        self.assertNotIn("TOP-SECRET-TEXT", listing)

        self.assertIsNone(
            database.get_capsule_content(capsule["id"], self.user_id)
        )

    def test_past_and_invalid_dates_are_rejected(self):
        self.create_capsule(unlock_date="2020-01-01")
        self.create_capsule(unlock_date="not-a-date")
        self.create_capsule(unlock_date="2999-01-01")
        self.assertEqual(database.list_capsules(self.user_id), [])

    def test_capsule_opens_after_its_time(self):
        self.create_capsule()
        capsule = database.list_capsules(self.user_id)[0]

        with database.get_connection() as connection:
            connection.execute(
                "UPDATE time_capsules SET unlock_at = '2020-01-01 09:00:00' "
                "WHERE id = ?", (capsule["id"],)
            )

        page = self.client.get(f"/capsules/{capsule['id']}").get_data(as_text=True)
        self.assertIn("TOP-SECRET-TEXT", page)
        self.assertIsNotNone(
            database.get_capsule_header(capsule["id"], self.user_id)["opened_at"]
        )

    def test_sealed_capsule_attachments_stay_locked(self):
        self.create_capsule()
        capsule = database.list_capsules(self.user_id)[0]
        media_id = database.create_media(
            self.user_id, "photo", "a.png", "a" * 32 + ".png", "image/png", 10,
            capsule_id=capsule["id"]
        )
        self.assertEqual(self.client.get(f"/media/{media_id}").status_code, 404)

    def test_other_users_cannot_see_or_delete_a_capsule(self):
        self.create_capsule()
        capsule = database.list_capsules(self.user_id)[0]
        stranger = self.other_client()
        self.assertEqual(stranger.get(f"/capsules/{capsule['id']}").status_code, 404)
        response = self.post(
            f"/capsules/{capsule['id']}/delete", client=stranger
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(database.list_capsules(self.user_id)), 1)

    def test_countdown_wording(self):
        from capsules import describe_capsule
        now = datetime(2026, 1, 1, 12, 0)

        def row(unlock, opened=None):
            return {"id": 1, "title": "t", "created_at": "2025-01-01 00:00:00",
                    "unlock_at": unlock, "opened_at": opened}

        self.assertEqual(
            describe_capsule(row("2026-01-11 12:00:00"), now)["countdown"],
            "Opens in 10 days"
        )
        self.assertEqual(
            describe_capsule(row("2026-01-02 12:00:00"), now)["countdown"],
            "Opens in 1 day"
        )
        self.assertEqual(
            describe_capsule(row("2025-12-01 12:00:00"), now)["state"], "ready"
        )
        self.assertEqual(
            describe_capsule(row("2025-12-01 12:00:00", "x"), now)["state"], "opened"
        )


# ============================================================
class AssistantTests(unittest.TestCase):

    URLS = {
        "entry": "/entries/__ID__", "new_entry": "/entries/new",
        "entries": "/entries", "calendar": "/calendar",
        "settings": "/settings", "capsules": "/capsules",
        "dashboard": "/dashboard"
    }
    TODAY = date(2026, 11, 21)

    @staticmethod
    def entry(i, title, content, day, favorite=0):
        return {"id": i, "title": title, "content": content,
                "created_at": f"{day} 10:00:00", "is_favorite": favorite}

    def setUp(self):
        self.entries = [
            self.entry(1, "Birthday dinner", "Cake, party and lechon with Mama.", "2026-11-20", 1),
            self.entry(2, "Rainy walk", "It rained all morning. Typhoon season.", "2026-11-19"),
            self.entry(3, "Exam stress", "Thesis defense next week, so tired.", "2026-10-14"),
            self.entry(4, "Last year", "Party at home.", "2025-11-21"),
            self.entry(5, "Pagod", "Sobrang pagod sa trabaho.", "2026-09-02"),
        ]

    def ask(self, message):
        return assistant.respond(message, self.entries, self.TODAY, self.URLS, seed=1)

    def titles(self, reply):
        return [r["title"] for r in reply["results"]]

    def test_search_finds_by_word_and_by_related_word(self):
        self.assertEqual(self.titles(self.ask("find lechon"))[0], "Birthday dinner")
        self.assertIn("Birthday dinner", self.titles(self.ask("kaarawan")))
        self.assertIn("Rainy walk", self.titles(self.ask("rainy day")))

    def test_filipino_related_words(self):
        reply = self.ask("tired")
        self.assertIn("Pagod", self.titles(reply))

    def test_date_phrases(self):
        self.assertEqual(
            self.titles(self.ask("memories from yesterday")), ["Birthday dinner"]
        )
        self.assertEqual(self.titles(self.ask("what did I write today")), [])
        # "memories from today" means "on this day" in earlier years.
        self.assertEqual(self.titles(self.ask("memories from today")), ["Last year"])
        self.assertEqual(
            self.titles(self.ask("what did I write in October")), ["Exam stress"]
        )
        self.assertIn("Birthday dinner", self.titles(self.ask("this month")))
        self.assertEqual(self.titles(self.ask("in 2025")), ["Last year"])

    def test_on_this_day_only_earlier_years(self):
        reply = self.ask("on this day")
        self.assertEqual(self.titles(reply), ["Last year"])

    def test_related_words_and_stats_and_help(self):
        self.assertIn("party", self.ask("related words to birthday")["related"])
        self.assertIn("5 memories", self.ask("stats")["reply"])
        self.assertIn("Edit", self.ask("how do I update an entry")["reply"])
        self.assertIn("search", self.ask("help")["reply"].lower())

    def test_help_is_not_triggered_inside_a_search(self):
        reply = self.ask("help me find my rainy day memory")
        self.assertIn("Rainy walk", self.titles(reply))

    def test_odd_input_is_safe(self):
        for text in ("", "   ", "<script>alert(1)</script>", "x" * 5000, "%%%", "ñandú"):
            reply = self.ask(text)
            self.assertIn("reply", reply)
            self.assertLessEqual(len(reply["reply"]), 2000)

    def test_only_the_callers_entries_are_searched(self):
        # The engine only sees what it is given, so the route must pass
        # nothing but the signed-in user's own entries.
        reply = assistant.respond("lechon", [], self.TODAY, self.URLS)
        self.assertEqual(reply["results"], [])


class AssistantRouteTests(FeatureTestCase):

    def api(self, client, message):
        token = csrf_from(client, "/assistant")
        return client.post(
            "/api/assistant", json={"message": message},
            headers={"X-CSRFToken": token}
        )

    def test_assistant_searches_only_my_memories(self):
        self.make_entry("Mine", "private banana story")
        stranger = self.other_client()

        reply = self.api(stranger, "banana").get_json()
        self.assertEqual(reply["results"], [])

        reply = self.api(self.client, "banana").get_json()
        self.assertEqual(len(reply["results"]), 1)

    def test_assistant_api_requires_login_csrf_and_json(self):
        anonymous = flask_app.test_client()
        response = anonymous.post("/api/assistant", json={"message": "hi"})
        self.assertIn(response.status_code, (400, 401))
        token = csrf_from(self.client, "/assistant")
        response = self.client.post(
            "/api/assistant", data="not json",
            headers={"X-CSRFToken": token, "Content-Type": "text/plain"}
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/assistant", json={"message": 123},
            headers={"X-CSRFToken": token}
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/assistant", json={"message": "hi"})
        self.assertEqual(response.status_code, 400)       # no CSRF token


# ============================================================
class SyncApiTests(FeatureTestCase):

    def sync(self, **overrides):
        token = csrf_from(self.client, "/settings")
        body = {
            "client_id": "abcdef123456", "title": "Offline memory",
            "content": "Written on the bus.",
            "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        body.update(overrides)
        return self.client.post(
            "/api/entries", json=body, headers={"X-CSRFToken": token}
        )

    def test_sync_is_idempotent(self):
        first = self.sync()
        second = self.sync()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["duplicate"])
        self.assertEqual(database.count_entries_by_user(self.user_id), 1)

    def test_same_client_id_is_per_user(self):
        self.sync()
        stranger = self.other_client()
        token = csrf_from(stranger, "/settings")
        response = stranger.post(
            "/api/entries",
            json={"client_id": "abcdef123456", "title": "t", "content": "c"},
            headers={"X-CSRFToken": token}
        )
        self.assertEqual(response.status_code, 201)

    def test_validation(self):
        self.assertEqual(self.sync(client_id="x").status_code, 400)
        self.assertEqual(self.sync(client_id="bad id with spaces").status_code, 400)
        self.assertEqual(self.sync(title="   ").status_code, 400)
        self.assertEqual(self.sync(content="").status_code, 400)
        self.assertEqual(self.sync(title="x" * 151).status_code, 400)
        self.assertEqual(self.sync(content="x" * 50_001).status_code, 400)
        self.assertEqual(self.sync(title=123).status_code, 400)
        self.assertEqual(database.count_entries_by_user(self.user_id), 0)

    def test_time_is_honoured_converted_and_clamped(self):
        response = self.sync(created_at_utc="2026-01-01T01:00:00Z")
        # More than the allowed age: falls back to "now".
        self.assertTrue(
            response.get_json()["entry"]["created_at"] >= "2026-01-01"
        )
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = self.sync(client_id="recent-0001", created_at_utc=recent).get_json()["entry"]
        expected = database.utc_iso_to_local_timestamp(recent)
        self.assertEqual(entry["created_at"], expected)
        future = self.sync(client_id="future-0001", created_at_utc="2999-01-01T00:00:00Z")
        self.assertLess(future.get_json()["entry"]["created_at"], "2100")

    def test_requires_csrf_and_login(self):
        response = self.client.post(
            "/api/entries", json={"client_id": "abcdef123456", "title": "t", "content": "c"}
        )
        self.assertEqual(response.status_code, 400)
        anonymous = flask_app.test_client()
        self.assertEqual(anonymous.get("/api/entries").status_code, 401)

    def test_expired_session_gets_json_401_not_a_redirect(self):
        token = csrf_from(self.client, "/settings")
        with database.get_connection() as connection:
            connection.execute(
                "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                (self.user_id,)
            )
        response = self.client.post(
            "/api/entries",
            json={"client_id": "abcdef123456", "title": "t", "content": "c"},
            headers={"X-CSRFToken": token}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

    def test_list_returns_only_my_entries(self):
        self.make_entry("Mine", "a")
        stranger = self.other_client()
        listing = stranger.get("/api/entries").get_json()
        self.assertEqual(listing["total"], 0)
        self.assertEqual(
            self.client.get("/api/entries").get_json()["total"], 1
        )
        self.assertEqual(
            self.client.get("/api/entries").headers["Cache-Control"], "no-store"
        )


# ============================================================
class VoiceUnlockTests(FeatureTestCase):

    @staticmethod
    def vector(offset=0.0, jitter=0.0, seed=0):
        import random
        rnd = random.Random(seed)
        base = [(i + 1) * 0.1 for i in range(28)]
        return [
            v + offset + (rnd.uniform(-jitter, jitter) if jitter else 0.0)
            for v in base
        ]

    def test_parse_features_validation(self):
        good = self.vector()
        self.assertEqual(len(voiceprint.parse_features(good)), 28)
        self.assertIsNone(voiceprint.parse_features(good[:27]))
        self.assertIsNone(voiceprint.parse_features("nope"))
        self.assertIsNone(voiceprint.parse_features(good[:27] + [float("nan")]))
        self.assertIsNone(voiceprint.parse_features(good[:27] + [float("inf")]))
        self.assertIsNone(voiceprint.parse_features(good[:27] + [1e9]))
        self.assertIsNone(voiceprint.parse_features(good[:27] + [True]))
        self.assertIsNone(voiceprint.parse_features(good[:27] + ["1"]))

    def test_enrollment_needs_five_consistent_recordings(self):
        same = [self.vector(jitter=0.02, seed=i) for i in range(5)]
        template = voiceprint.build_template(same)
        self.assertEqual(len(template["mean"]), 28)
        self.assertGreaterEqual(template["threshold"], voiceprint.THRESHOLD_FLOOR)
        self.assertLessEqual(template["threshold"], voiceprint.THRESHOLD_CEILING)

        with self.assertRaises(ValueError):
            voiceprint.build_template(same[:3])

        mixed = [self.vector(offset=o) for o in (0, 5, -5, 9, -9)]
        with self.assertRaises(ValueError):
            voiceprint.build_template(mixed)

    def test_matching_accepts_same_and_rejects_far_voice(self):
        same = [self.vector(jitter=0.02, seed=i) for i in range(5)]
        template = voiceprint.build_template(same)
        self.assertTrue(voiceprint.matches(template, self.vector(jitter=0.02, seed=99)))
        self.assertFalse(voiceprint.matches(template, self.vector(offset=3.0)))
        self.assertFalse(voiceprint.matches({}, self.vector()))
        self.assertFalse(voiceprint.matches({"mean": "x", "threshold": 1}, self.vector()))

    def post_json(self, path, body):
        token = csrf_from(self.client, "/settings")
        return self.client.post(path, json=body, headers={"X-CSRFToken": token})

    def enroll(self, password="password123"):
        samples = [
            {"features": self.vector(jitter=0.02, seed=i), "voiced_ms": 1500}
            for i in range(5)
        ]
        return self.post_json(
            "/api/voice/enroll", {"password": password, "samples": samples}
        )

    def test_enroll_requires_correct_password(self):
        self.assertEqual(self.enroll("wrong-password").status_code, 403)
        self.assertIsNone(database.get_voice_profile(self.user_id))
        self.assertEqual(self.enroll().status_code, 200)
        profile = database.get_voice_profile(self.user_id)
        self.assertEqual(set(json.loads(profile["template"])),
                         {"version", "mean", "threshold", "samples"})

    def test_capsule_opening_is_gated_and_password_is_the_fallback(self):
        self.enroll()
        with self.client.session_transaction() as session:
            session.pop("step_up_until", None)

        capsule_id = database.create_capsule(
            self.user_id, "Old", "GATED-TEXT", "2020-01-01 09:00:00"
        )

        response = self.client.get(f"/capsules/{capsule_id}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/unlock", response.headers["Location"])

        token = csrf_from(self.client, "/unlock?next=/capsules/%d" % capsule_id)
        wrong = self.client.post("/unlock/password", data={
            "csrf_token": token, "password": "nope", "next": f"/capsules/{capsule_id}"
        })
        self.assertIn("/unlock", wrong.headers["Location"])

        right = self.client.post("/unlock/password", data={
            "csrf_token": token, "password": "password123",
            "next": f"/capsules/{capsule_id}"
        })
        self.assertTrue(right.headers["Location"].endswith(f"/capsules/{capsule_id}"))
        page = self.client.get(f"/capsules/{capsule_id}").get_data(as_text=True)
        self.assertIn("GATED-TEXT", page)

    def test_unlock_redirect_target_is_validated(self):
        self.enroll()
        with self.client.session_transaction() as session:
            session.pop("step_up_until", None)
        for evil in ("https://evil.example/", "//evil.example", "/\\evil.example"):
            with self.client.session_transaction() as session:
                session.pop("step_up_until", None)

            token = csrf_from(self.client, "/unlock?next=/capsules")
            response = self.client.post("/unlock/password", data={
                "csrf_token": token, "password": "password123", "next": evil
            })
            self.assertNotIn("evil", response.headers["Location"])

    def test_verify_is_rate_limited_and_never_leaks_a_score(self):
        self.enroll()
        far = {"features": self.vector(offset=3.0), "voiced_ms": 1500}
        codes = []
        for _ in range(8):
            response = self.post_json("/api/voice/verify", far)
            codes.append(response.status_code)
            self.assertNotIn("distance", response.get_data(as_text=True))
            self.assertNotIn("score", response.get_data(as_text=True))
        self.assertIn(429, codes)

    def test_removing_voice_unlock_needs_password(self):
        self.enroll()
        self.assertEqual(self.post_json("/api/voice/delete", {"password": "x"}).status_code, 403)
        self.assertIsNotNone(database.get_voice_profile(self.user_id))
        self.assertEqual(
            self.post_json("/api/voice/delete", {"password": "password123"}).status_code, 200
        )
        self.assertIsNone(database.get_voice_profile(self.user_id))


# ============================================================
class SecurityTests(FeatureTestCase):

    def test_security_headers_and_strict_csp(self):
        response = self.client.get("/dashboard")
        policy = response.headers["Content-Security-Policy"]

        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'self'", policy)
        self.assertIn("microphone=(self)", response.headers["Permissions-Policy"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_pages_have_no_inline_scripts_or_styles(self):
        for path in ("/dashboard", "/entries", "/entries/new", "/calendar",
                     "/settings", "/assistant", "/capsules", "/capsules/new",
                     "/offline", "/", "/login", "/register"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIsNone(
                re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path
            )
            self.assertIsNone(re.search(r"<style", html), path)
            self.assertIsNone(re.search(r'\sstyle="', html), path)
            self.assertIsNone(re.search(r"\son(click|load|error)=", html, re.I), path)

    def test_every_post_route_requires_csrf(self):
        for path in ("/entries/new", "/capsules/new", "/logout",
                     "/settings/change-password", "/unlock/password"):
            response = self.client.post(path, data={"title": "x"})
            self.assertEqual(response.status_code, 400, path)

    def test_every_private_page_requires_login(self):
        anonymous = flask_app.test_client()
        for path in ("/dashboard", "/entries", "/entries/new", "/entries/1",
                     "/entries/1/edit", "/calendar", "/settings", "/assistant",
                     "/capsules", "/capsules/new", "/capsules/1", "/media/1",
                     "/unlock"):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/login", response.headers["Location"], path)

    def test_login_next_cannot_be_an_open_redirect(self):
        for evil in ("https://evil.example", "//evil.example", "/\\evil.example",
                     "javascript:alert(1)"):
            client = flask_app.test_client()
            token = csrf_from(client, "/login")
            response = client.post("/login", data={
                "csrf_token": token, "email": self.email,
                "password": "password123", "next": evil
            })
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("evil", response.headers["Location"])
            self.assertNotIn("javascript", response.headers["Location"])

        client = flask_app.test_client()
        token = csrf_from(client, "/login")
        response = client.post("/login", data={
            "csrf_token": token, "email": self.email,
            "password": "password123", "next": "/entries?page=2"
        })
        self.assertTrue(response.headers["Location"].endswith("/entries?page=2"))

    def test_registration_input_limits(self):
        client = flask_app.test_client()
        token = csrf_from(client, "/register")

        def attempt(**fields):
            data = {"csrf_token": token, "full_name": "A", "email": "a@b.co",
                    "password": "password123", "confirm_password": "password123"}
            data.update(fields)
            client.post("/register", data=data)

        attempt(email="not-an-email")
        attempt(full_name="x" * 101, email="long@name.co")
        attempt(password="p" * 129, confirm_password="p" * 129, email="longpw@b.co")
        for email in ("a@b.co", "long@name.co", "longpw@b.co", "not-an-email"):
            self.assertIsNone(database.get_user_by_email(email), email)

    def test_huge_password_at_login_is_refused_quickly(self):
        client = flask_app.test_client()
        token = csrf_from(client, "/login")
        response = client.post("/login", data={
            "csrf_token": token, "email": self.email, "password": "p" * 3000
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_errors_are_friendly_and_api_errors_are_json(self):
        page = self.client.get("/definitely-not-here")
        self.assertEqual(page.status_code, 404)
        self.assertIn("We couldn", page.get_data(as_text=True))
        self.assertNotIn("Traceback", page.get_data(as_text=True))

        response = self.client.post("/api/entries", data="x")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_healthcheck_and_pwa_files(self):
        anonymous = flask_app.test_client()
        self.assertEqual(anonymous.get("/healthz").get_json()["status"], "ok")
        worker = anonymous.get("/sw.js")
        self.assertEqual(worker.status_code, 200)
        self.assertIn("javascript", worker.headers["Content-Type"])
        self.assertEqual(worker.headers["Service-Worker-Allowed"], "/")
        shell = worker.get_data(as_text=True)
        for private in ('"/dashboard"', '"/entries', '"/api/', '"/media/', '"/capsules'):
            self.assertNotIn(private, shell)
        manifest = anonymous.get("/manifest.webmanifest").get_json()
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(anonymous.get("/offline").status_code, 200)

    def test_uploaded_files_are_not_reachable_via_static(self):
        anonymous = flask_app.test_client()
        for path in ("/static/../alaala.db", "/static/%2e%2e/app.py",
                     "/static/../uploads/1/x.png"):
            self.assertEqual(anonymous.get(path).status_code, 404, path)


# ============================================================
class UpgradeTests(unittest.TestCase):

    def test_existing_database_is_upgraded_without_losing_anything(self):
        path = Path(tempfile.gettempdir()) / f"everstory_old_{uuid.uuid4().hex}.db"

        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE diary_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                title TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE app_migrations (migration_key TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO app_migrations VALUES ('diary_timestamps_to_philippine_time', 'x');
            INSERT INTO users (full_name, email, password_hash) VALUES ('Old User', 'old@example.com', 'hash');
            INSERT INTO diary_entries (user_id, title, content, created_at, updated_at)
                VALUES (1, 'Old memory', 'Written long ago', '2025-03-04 05:06:07', '2025-03-04 05:06:07');
        """)
        connection.commit()
        connection.close()

        previous = database.DATABASE_PATH
        database.DATABASE_PATH = path

        try:
            database.initialize_database()
            database.initialize_database()      # running twice must be harmless

            entry = database.get_entries_by_user(1)[0]
            self.assertEqual(
                (entry["title"], entry["content"], entry["created_at"], entry["is_favorite"]),
                ("Old memory", "Written long ago", "2025-03-04 05:06:07", 0)
            )
            self.assertEqual(database.get_user_by_email("old@example.com")["full_name"], "Old User")

            connection = sqlite3.connect(path)
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertTrue({"entry_media", "time_capsules", "voice_profiles"} <= tables)
        finally:
            database.DATABASE_PATH = previous
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
