
"""
Everstory - Application Regression Tests

Tests use an isolated temporary SQLite database.
The original project database is never modified.
"""

import os
import sys
import tempfile
import unittest
import uuid
import time

from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash


# ============================================================
# PROJECT AND TEST DATABASE CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The tests need a secret key to import the app. A throw-away key is used
# only when none is configured, and only inside the test run.
if not (
    os.environ.get("EVERSTORY_SECRET_KEY")
    or os.environ.get("ALAALA_SECRET_KEY")
):
    os.environ["EVERSTORY_SECRET_KEY"] = (
        "test-only-secret-key-never-use-in-production-0123456789"
    )

import database


TEST_DATABASE_PATH = (
    Path(tempfile.gettempdir())
    / f"everstory_regression_{uuid.uuid4().hex}.db"
)

database.DATABASE_PATH = TEST_DATABASE_PATH

import app as app_module

app = app_module.app

app.config.update(
    TESTING=True,
    SECRET_KEY="everstory-automated-test-secret"
)

assert (
    database.DATABASE_PATH.resolve()
    != (PROJECT_ROOT / "alaala.db").resolve()
), "SAFETY ERROR: Tests must not use the original database."


# ============================================================
# TEST CASES
# ============================================================

class EverstoryApplicationTests(unittest.TestCase):

    def setUp(self):

        with database.get_connection() as connection:
            connection.execute(
                "DELETE FROM login_attempts"
            )

        self.client = app.test_client()

        self.email = (
            f"test-{uuid.uuid4().hex}@example.com"
        )

        self.password = "TestPassword123!"

        self.user_id = database.create_user(
            "Everstory Test User",
            self.email,
            generate_password_hash(self.password)
        )

        response = self.post_with_csrf(
            self.client,
            "/login",
            {
                "email": self.email,
                "password": self.password
            },
            csrf_page="/login"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)

    # --------------------------------------------------------
    # CSRF TEST HELPERS
    # --------------------------------------------------------

    def get_csrf_token(self, client, page):

        page_response = client.get(page)

        self.assertEqual(
            page_response.status_code,
            200,
            f"Could not load CSRF token page: {page}"
        )

        with client.session_transaction() as test_session:
            token = test_session.get("_csrf_token")

        self.assertIsInstance(token, str)
        self.assertTrue(token)

        return token

    def post_with_csrf(
        self,
        client,
        endpoint,
        data,
        csrf_page
    ):

        token = self.get_csrf_token(
            client,
            csrf_page
        )

        form_data = dict(data)
        form_data["csrf_token"] = token

        return client.post(
            endpoint,
            data=form_data
        )

    def attempt_login(self, client, email, password):

        return self.post_with_csrf(
            client,
            "/login",
            {
                "email": email,
                "password": password
            },
            csrf_page="/login"
        )

    # --------------------------------------------------------
    # 1. PUBLIC PAGE TESTS
    # --------------------------------------------------------

    def test_public_pages_load(self):

        public_pages = [
            "/",
            "/login",
            "/register"
        ]

        for page in public_pages:

            with self.subTest(page=page):

                response = self.client.get(page)

                self.assertEqual(
                    response.status_code,
                    200
                )

    # --------------------------------------------------------
    # 2. CSRF PROTECTION TESTS
    # --------------------------------------------------------

    def test_post_without_csrf_token_is_rejected(self):

        response = self.client.post(
            "/login",
            data={
                "email": self.email,
                "password": self.password
            }
        )

        self.assertEqual(response.status_code, 400)

    def test_post_with_invalid_csrf_token_is_rejected(self):

        self.client.get("/login")

        response = self.client.post(
            "/login",
            data={
                "email": self.email,
                "password": self.password,
                "csrf_token": "invalid-csrf-token"
            }
        )

        self.assertEqual(response.status_code, 400)

    # --------------------------------------------------------
    # 3. REGISTRATION TEST
    # --------------------------------------------------------

    def test_registration_creates_account(self):

        email = (
            f"registration-{uuid.uuid4().hex}@example.com"
        )

        response = self.post_with_csrf(
            self.client,
            "/register",
            {
                "full_name": "Registration Test",
                "email": email,
                "password": "Register123!",
                "confirm_password": "Register123!"
            },
            csrf_page="/register"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        created_user = database.get_user_by_email(email)

        self.assertIsNotNone(created_user)

        duplicate_response = self.post_with_csrf(
            self.client,
            "/register",
            {
                "full_name": "Duplicate Test",
                "email": email,
                "password": "Register123!",
                "confirm_password": "Register123!"
            },
            csrf_page="/register"
        )

        self.assertEqual(duplicate_response.status_code, 302)
        self.assertIn("/register", duplicate_response.location)

    # --------------------------------------------------------
    # 4. AUTHENTICATION AND ACCESS CONTROL
    # --------------------------------------------------------

    def test_protected_pages_require_login(self):

        protected_pages = [
            "/dashboard",
            "/settings",
            "/calendar",
            "/entries",
            "/entries/new"
        ]

        logged_out_client = app.test_client()

        for page in protected_pages:

            with self.subTest(page=page):

                response = logged_out_client.get(page)

                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

        api_response = logged_out_client.get(
            "/api/preferences"
        )

        self.assertEqual(api_response.status_code, 401)

    def test_login_and_logout(self):

        client = app.test_client()

        login_response = self.attempt_login(
            client,
            self.email,
            self.password
        )

        self.assertEqual(login_response.status_code, 302)
        self.assertIn("/dashboard", login_response.location)

        dashboard_response = client.get("/dashboard")

        self.assertEqual(dashboard_response.status_code, 200)

        csrf_token = self.get_csrf_token(
            client,
            "/dashboard"
        )

        logout_response = client.post(
            "/logout",
            data={
                "csrf_token": csrf_token
            }
        )

        self.assertEqual(logout_response.status_code, 302)
        self.assertIn("/", logout_response.location)

        protected_response = client.get("/dashboard")

        self.assertEqual(protected_response.status_code, 302)
        self.assertIn("/login", protected_response.location)

    def test_logout_rejects_get_requests(self):

        client = app.test_client()

        response = client.get("/logout")

        self.assertEqual(response.status_code, 405)

    def test_logout_rejects_missing_csrf_token(self):

        client = app.test_client()

        self.attempt_login(
            client,
            self.email,
            self.password
        )

        response = client.post("/logout")

        self.assertEqual(response.status_code, 400)

    def test_logout_rejects_invalid_csrf_token(self):

        client = app.test_client()

        self.attempt_login(
            client,
            self.email,
            self.password
        )

        response = client.post(
            "/logout",
            data={
                "csrf_token": "invalid-csrf-token"
            }
        )

        self.assertEqual(response.status_code, 400)

    # --------------------------------------------------------
    # 5. DIARY ENTRY TESTS
    # --------------------------------------------------------

    def test_create_and_display_diary_entry(self):

        title = "Automated Test Memory"
        content = "This memory was created by a regression test."

        response = self.post_with_csrf(
            self.client,
            "/entries/new",
            {
                "title": title,
                "content": content
            },
            csrf_page="/entries/new"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/entries", response.location)

        entries_response = self.client.get("/entries")

        self.assertEqual(entries_response.status_code, 200)

        page_content = entries_response.get_data(as_text=True)

        self.assertIn(title, page_content)
        self.assertIn(content, page_content)

    def test_empty_diary_entry_is_rejected(self):

        response = self.post_with_csrf(
            self.client,
            "/entries/new",
            {
                "title": "",
                "content": ""
            },
            csrf_page="/entries/new"
        )

        self.assertEqual(response.status_code, 200)

        entries = database.get_entries_by_user(self.user_id)

        self.assertEqual(len(entries), 0)

    # --------------------------------------------------------
    # 6. ACCOUNT PRIVACY TEST
    # --------------------------------------------------------

    def test_users_cannot_see_each_others_memories(self):

        second_email = (
            f"second-{uuid.uuid4().hex}@example.com"
        )

        second_password = "SecondPassword123!"

        second_user_id = database.create_user(
            "Second Test User",
            second_email,
            generate_password_hash(second_password)
        )

        database.create_entry(
            self.user_id,
            "Private Memory A",
            "This belongs only to account A."
        )

        database.create_entry(
            second_user_id,
            "Private Memory B",
            "This belongs only to account B."
        )

        second_client = app.test_client()

        login_response = self.attempt_login(
            second_client,
            second_email,
            second_password
        )

        self.assertEqual(login_response.status_code, 302)

        account_a_page = self.client.get("/entries")
        account_b_page = second_client.get("/entries")

        account_a_content = account_a_page.get_data(as_text=True)
        account_b_content = account_b_page.get_data(as_text=True)

        self.assertIn("Private Memory A", account_a_content)
        self.assertNotIn("Private Memory B", account_a_content)

        self.assertIn("Private Memory B", account_b_content)
        self.assertNotIn("Private Memory A", account_b_content)

    # --------------------------------------------------------
    # 7. SETTINGS AND PERSONALIZATION TESTS
    # --------------------------------------------------------

    def test_appearance_settings_are_saved(self):

        response = self.post_with_csrf(
            self.client,
            "/settings",
            {
                "form_type": "appearance",
                "theme_mode": "dark"
            },
            csrf_page="/settings"
        )

        self.assertEqual(response.status_code, 302)

        api_response = self.client.get("/api/preferences")

        self.assertEqual(api_response.status_code, 200)

        preferences = api_response.get_json()

        self.assertEqual(preferences["theme_mode"], "dark")

    def test_personalization_settings_are_saved(self):

        response = self.post_with_csrf(
            self.client,
            "/settings",
            {
                "form_type": "preferences",
                "seasonal_theme": "summer",
                "font_style": "serif",
                "text_size": "large",
                "reduced_animations": "1"
            },
            csrf_page="/settings"
        )

        self.assertEqual(response.status_code, 302)

        api_response = self.client.get("/api/preferences")

        self.assertEqual(api_response.status_code, 200)

        preferences = api_response.get_json()

        self.assertEqual(preferences["seasonal_theme"], "summer")
        self.assertEqual(preferences["font_style"], "serif")
        self.assertEqual(preferences["text_size"], "large")
        self.assertTrue(preferences["reduced_animations"])

    # --------------------------------------------------------
    # 8. CALENDAR TEST
    # --------------------------------------------------------

    def test_calendar_displays_user_memory(self):

        title = "Calendar Regression Memory"

        database.create_entry(
            self.user_id,
            title,
            "Memory used to test the calendar."
        )

        entries = database.get_entries_by_user(self.user_id)

        self.assertGreater(len(entries), 0)

        entry_datetime = datetime.fromisoformat(
            str(entries[0]["created_at"])
        )

        entry_date = entry_datetime.date()

        response = self.client.get(
            "/calendar",
            query_string={
                "year": entry_date.year,
                "month": entry_date.month,
                "date": entry_date.isoformat()
            }
        )

        self.assertEqual(response.status_code, 200)

        page_content = response.get_data(as_text=True)

        self.assertIn(title, page_content)

    # --------------------------------------------------------
    # 9. SESSION EXPIRATION AND ACTIVITY TESTS
    # --------------------------------------------------------

    def test_session_expires_after_thirty_minutes_inactivity(self):

        with self.client.session_transaction() as test_session:
            test_session["_last_activity"] = time.time() - 1801

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        with self.client.session_transaction() as test_session:
            self.assertNotIn("user_id", test_session)

    def test_active_session_refreshes_activity_timestamp(self):

        previous_activity = time.time() - 60

        with self.client.session_transaction() as test_session:
            test_session["_last_activity"] = previous_activity

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as test_session:
            refreshed_activity = test_session.get("_last_activity")

        self.assertIsInstance(refreshed_activity, (int, float))
        self.assertGreater(refreshed_activity, previous_activity)

    def test_session_with_invalid_activity_timestamp_is_rejected(self):

        with self.client.session_transaction() as test_session:
            test_session["_last_activity"] = "invalid-timestamp"

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        with self.client.session_transaction() as test_session:
            self.assertNotIn("user_id", test_session)

    # --------------------------------------------------------
    # 10. PASSWORD SECURITY TEST
    # --------------------------------------------------------

    def test_password_change_invalidates_other_sessions(self):

        second_device = app.test_client()

        second_login = self.attempt_login(
            second_device,
            self.email,
            self.password
        )

        self.assertEqual(second_login.status_code, 302)

        new_password = "NewPassword456!"

        change_response = self.post_with_csrf(
            self.client,
            "/settings/change-password",
            {
                "current_password": self.password,
                "new_password": new_password,
                "confirm_password": new_password
            },
            csrf_page="/settings"
        )

        self.assertEqual(change_response.status_code, 302)

        current_device_response = self.client.get("/settings")

        self.assertEqual(current_device_response.status_code, 200)

        second_device_response = second_device.get("/dashboard")

        self.assertEqual(second_device_response.status_code, 302)
        self.assertIn("/login", second_device_response.location)

        old_password_client = app.test_client()

        old_login = self.attempt_login(
            old_password_client,
            self.email,
            self.password
        )

        self.assertEqual(old_login.status_code, 302)
        self.assertIn("/login", old_login.location)

        new_password_client = app.test_client()

        new_login = self.attempt_login(
            new_password_client,
            self.email,
            new_password
        )

        self.assertEqual(new_login.status_code, 302)
        self.assertIn("/dashboard", new_login.location)

    # --------------------------------------------------------
    # 11. LOGIN RATE-LIMITING TESTS
    # --------------------------------------------------------

    def test_login_is_blocked_after_five_failed_attempts(self):

        client = app.test_client()

        for attempt in range(5):

            response = self.attempt_login(
                client,
                self.email,
                "WrongPassword123!"
            )

            self.assertEqual(
                response.status_code,
                302,
                f"Unexpected response on failed attempt {attempt + 1}"
            )

        response = self.attempt_login(
            client,
            self.email,
            "WrongPassword123!"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        blocked_page = client.get(
            response.location,
            follow_redirects=True
        )

        self.assertIn(
            "Too many unsuccessful login attempts",
            blocked_page.get_data(as_text=True)
        )

    def test_login_is_blocked_after_twenty_ip_attempts(self):

        client = app.test_client()

        for attempt in range(20):

            unique_email = (
                f"ip-test-{attempt}-{uuid.uuid4().hex}@example.com"
            )

            response = self.attempt_login(
                client,
                unique_email,
                "WrongPassword123!"
            )

            self.assertEqual(
                response.status_code,
                302,
                f"Unexpected IP limit response at attempt {attempt + 1}"
            )

        final_email = (
            f"ip-final-{uuid.uuid4().hex}@example.com"
        )

        response = self.attempt_login(
            client,
            final_email,
            "WrongPassword123!"
        )

        self.assertEqual(response.status_code, 302)

        blocked_page = client.get(
            response.location,
            follow_redirects=True
        )

        self.assertIn(
            "Too many unsuccessful login attempts",
            blocked_page.get_data(as_text=True)
        )

    def test_login_rate_limit_expires_after_window(self):

        client = app.test_client()

        for _ in range(5):

            response = self.attempt_login(
                client,
                self.email,
                "WrongPassword123!"
            )

            self.assertEqual(response.status_code, 302)

        expired_timestamp = 946684800.0

        with database.get_connection() as connection:

            connection.execute(
                """
                UPDATE login_attempts
                SET attempted_at = ?
                WHERE email = ?
                """,
                (
                    expired_timestamp,
                    self.email
                )
            )

        response = self.attempt_login(
            client,
            self.email,
            self.password
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)

    def test_successful_login_clears_failed_attempts(self):

        client = app.test_client()

        for _ in range(4):

            response = self.attempt_login(
                client,
                self.email,
                "WrongPassword123!"
            )

            self.assertEqual(response.status_code, 302)

        response = self.attempt_login(
            client,
            self.email,
            self.password
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)

        ip_address = client.environ_base.get(
            "REMOTE_ADDR",
            "127.0.0.1"
        )

        self.assertFalse(
            database.is_login_rate_limited(
                self.email,
                ip_address,
                max_attempts=5,
                window_seconds=900,
                max_ip_attempts=20
            )
        )

    def test_login_error_message_does_not_reveal_unknown_email(self):

        existing_account_client = app.test_client()
        unknown_account_client = app.test_client()

        existing_response = self.attempt_login(
            existing_account_client,
            self.email,
            "WrongPassword123!"
        )

        self.assertEqual(existing_response.status_code, 302)

        existing_page = existing_account_client.get(
            existing_response.location,
            follow_redirects=True
        )

        unknown_email = (
            f"unknown-{uuid.uuid4().hex}@example.com"
        )

        unknown_response = self.attempt_login(
            unknown_account_client,
            unknown_email,
            "WrongPassword123!"
        )

        self.assertEqual(unknown_response.status_code, 302)

        unknown_page = unknown_account_client.get(
            unknown_response.location,
            follow_redirects=True
        )

        existing_content = existing_page.get_data(as_text=True)
        unknown_content = unknown_page.get_data(as_text=True)

        generic_message = "Incorrect email or password."

        self.assertIn(generic_message, existing_content)
        self.assertIn(generic_message, unknown_content)

    # --------------------------------------------------------
    # 12. HTTP SECURITY HEADER TESTS
    # --------------------------------------------------------

    def test_http_security_headers_are_present(self):

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.headers.get("X-Content-Type-Options"),
            "nosniff"
        )

        self.assertEqual(
            response.headers.get("X-Frame-Options"),
            "SAMEORIGIN"
        )

        self.assertEqual(
            response.headers.get("Referrer-Policy"),
            "strict-origin-when-cross-origin"
        )

    # --------------------------------------------------------
    # 13. REQUEST SIZE LIMIT TEST
    # --------------------------------------------------------

    def test_oversized_request_is_rejected(self):

        original_limit = app.config["MAX_CONTENT_LENGTH"]

        try:
            # Use a small temporary limit for this test.
            # This avoids allocating a 5 MB request.
            app.config["MAX_CONTENT_LENGTH"] = 64

            response = self.client.post(
                "/login",
                data="x" * 128,
                content_type="application/x-www-form-urlencoded"
            )

            self.assertEqual(response.status_code, 413)

        finally:
            app.config["MAX_CONTENT_LENGTH"] = original_limit

    # --------------------------------------------------------
    # 14. SESSION COOKIE SECURITY TESTS
    # --------------------------------------------------------

    def test_session_cookie_has_expected_security_attributes(self):

        client = app.test_client()

        response = self.attempt_login(
            client,
            self.email,
            self.password
        )

        self.assertEqual(response.status_code, 302)

        set_cookie_header = response.headers.get("Set-Cookie")

        self.assertIsNotNone(set_cookie_header)

        self.assertIn("HttpOnly", set_cookie_header)
        self.assertIn("SameSite=Lax", set_cookie_header)

        # Local development uses HTTP.
        # Secure must be enabled when deployed over HTTPS.
        self.assertNotIn("; Secure", set_cookie_header)

    def test_login_creates_permanent_session(self):

        client = app.test_client()

        response = self.attempt_login(
            client,
            self.email,
            self.password
        )

        self.assertEqual(response.status_code, 302)

        with client.session_transaction() as test_session:

            self.assertTrue(test_session.permanent)

            self.assertIn("user_id", test_session)

            self.assertIn("_last_activity", test_session)

        self.assertEqual(
            float(app.config["PERMANENT_SESSION_LIFETIME"]),
            1800.0
        )


# ============================================================
# TEST DATABASE CLEANUP
# ============================================================

def tearDownModule():

    for suffix in ("", "-wal", "-shm"):

        temporary_file = Path(
            str(TEST_DATABASE_PATH) + suffix
        )

        try:
            if temporary_file.exists():
                temporary_file.unlink()

        except OSError:
            pass


if __name__ == "__main__":

    unittest.main(verbosity=2)
