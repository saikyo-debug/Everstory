
import sqlite3
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import database


class DatabaseSecurityTests(unittest.TestCase):

    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temp_directory.name) / "test_everstory.db"
        )

        self.database_path_patch = patch.object(
            database,
            "DATABASE_PATH",
            self.database_path
        )
        self.database_path_patch.start()

        database.initialize_database()

        self.user_id = database.create_user(
            "Test User",
            "test@example.com",
            "test_password_hash"
        )

    def tearDown(self):
        self.database_path_patch.stop()
        self.temp_directory.cleanup()

    def test_duplicate_email_rejected_case_insensitively(self):
        with self.assertRaises(sqlite3.IntegrityError):
            database.create_user(
                "Another User",
                "TEST@EXAMPLE.COM",
                "another_password_hash"
            )

    def test_password_update_increments_session_version(self):
        original_user = database.get_user_by_id(self.user_id)
        original_version = original_user["session_version"]

        new_version = database.update_user_password_hash(
            self.user_id,
            "new_password_hash"
        )

        updated_user = database.get_user_by_id(self.user_id)

        self.assertEqual(
            new_version,
            original_version + 1
        )

        self.assertEqual(
            updated_user["session_version"],
            original_version + 1
        )

    def test_password_update_for_missing_user_returns_none(self):
        result = database.update_user_password_hash(
            999999,
            "new_password_hash"
        )

        self.assertIsNone(result)

    def test_user_cannot_modify_another_users_entry(self):
        another_user_id = database.create_user(
            "Another User",
            "another@example.com",
            "another_password_hash"
        )

        entry_id = database.create_entry(
            self.user_id,
            "Private Memory",
            "This memory belongs to the first user."
        )

        updated = database.update_entry(
            entry_id,
            another_user_id,
            "Unauthorized Change",
            "This should not replace the original memory."
        )

        deleted = database.delete_entry(
            entry_id,
            another_user_id
        )

        original_entry = database.get_entry_by_id_and_user(
            entry_id,
            self.user_id
        )

        self.assertFalse(updated)
        self.assertFalse(deleted)
        self.assertIsNotNone(original_entry)

        self.assertEqual(
            original_entry["title"],
            "Private Memory"
        )

    def test_invalid_personalization_values_are_rejected(self):
        invalid_theme = database.update_user_personalization(
            self.user_id,
            "invalid_theme",
            "modern",
            "medium",
            False
        )

        invalid_font = database.update_user_personalization(
            self.user_id,
            "classic",
            "invalid_font",
            "medium",
            False
        )

        invalid_text_size = database.update_user_personalization(
            self.user_id,
            "classic",
            "modern",
            "extra_large",
            False
        )

        invalid_animation_setting = (
            database.update_user_personalization(
                self.user_id,
                "classic",
                "modern",
                "medium",
                2
            )
        )

        self.assertFalse(invalid_theme)
        self.assertFalse(invalid_font)
        self.assertFalse(invalid_text_size)
        self.assertFalse(invalid_animation_setting)

    def test_failed_user_creation_rolls_back_account_insert(self):
        with database.get_connection() as connection:
            connection.execute("""
                CREATE TRIGGER reject_preference_creation
                BEFORE INSERT ON user_preferences
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'Simulated preference creation failure'
                    );
                END
            """)

        with self.assertRaises(sqlite3.IntegrityError):
            database.create_user(
                "Rollback Test",
                "rollback@example.com",
                "test_password_hash"
            )

        with database.get_connection() as connection:
            user = connection.execute("""
                SELECT id
                FROM users
                WHERE email = ?
            """, ("rollback@example.com",)).fetchone()

        self.assertIsNone(user)


if __name__ == "__main__":
    unittest.main()
