
import sqlite3
import time

from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import config


# The database file keeps its original name (alaala.db) so existing
# diaries are found without any migration of the file itself.
DATABASE_PATH = Path(config.DEFAULT_DATABASE_PATH)

# Philippine Standard Time (UTC+8)
PHILIPPINE_TIMEZONE = timezone(timedelta(hours=8))

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_local_time():
    """Return the current Philippine time as a timezone-aware datetime."""
    return datetime.now(PHILIPPINE_TIMEZONE)


def get_local_timestamp():
    """Return Philippine time in the format used by the diary database."""
    return get_local_time().replace(
        tzinfo=None
    ).isoformat(sep=" ", timespec="seconds")


def utc_iso_to_local_timestamp(value):
    """
    Convert an ISO-8601 UTC string (for example "2026-11-21T05:30:00Z",
    as sent by a browser) into the Philippine-time format used by the
    database. Returns None when the value cannot be understood.
    """

    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()

    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(PHILIPPINE_TIMEZONE).replace(
        tzinfo=None
    ).isoformat(sep=" ", timespec="seconds")


@contextmanager
def get_connection():
    """
    Open a SQLite connection with automatic transaction handling
    and guaranteed connection cleanup.

    Successful operations are committed.
    Failed operations are rolled back.
    The connection is always closed.
    """

    connection = sqlite3.connect(DATABASE_PATH, timeout=10)

    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")

        with connection:
            yield connection

    finally:
        connection.close()


def initialize_database():
    with get_connection() as connection:

        # USER ACCOUNTS
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # USER TABLE MIGRATIONS
        existing_user_columns = {
            column["name"]
            for column in connection.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        if "session_version" not in existing_user_columns:
            connection.execute("""
                ALTER TABLE users
                ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0
            """)

        # LOGIN ATTEMPT TRACKING
        # Stores failed login attempts for persistent rate limiting.
        # This table does not modify or delete existing user accounts.

        connection.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                attempted_at REAL NOT NULL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_login_attempts_email_ip_time
            ON login_attempts (
                email,
                ip_address,
                attempted_at
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_login_attempts_ip_time
            ON login_attempts (
                ip_address,
                attempted_at
            )
        """)

        # USER PREFERENCES
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,

                theme_name TEXT NOT NULL DEFAULT 'alaala',
                background_color TEXT NOT NULL DEFAULT '#F8F8F5',
                accent_color TEXT NOT NULL DEFAULT '#958CE8',
                theme_mode TEXT NOT NULL DEFAULT 'system',

                seasonal_theme TEXT NOT NULL DEFAULT 'classic',
                font_style TEXT NOT NULL DEFAULT 'modern',
                text_size TEXT NOT NULL DEFAULT 'medium',
                reduced_animations INTEGER NOT NULL DEFAULT 0,

                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # USER PREFERENCE MIGRATIONS
        existing_columns = {
            column["name"]
            for column in connection.execute(
                "PRAGMA table_info(user_preferences)"
            ).fetchall()
        }

        migrations = {
            "theme_mode": """
                ALTER TABLE user_preferences
                ADD COLUMN theme_mode TEXT NOT NULL DEFAULT 'system'
            """,
            "seasonal_theme": """
                ALTER TABLE user_preferences
                ADD COLUMN seasonal_theme TEXT NOT NULL DEFAULT 'classic'
            """,
            "font_style": """
                ALTER TABLE user_preferences
                ADD COLUMN font_style TEXT NOT NULL DEFAULT 'modern'
            """,
            "text_size": """
                ALTER TABLE user_preferences
                ADD COLUMN text_size TEXT NOT NULL DEFAULT 'medium'
            """,
            "reduced_animations": """
                ALTER TABLE user_preferences
                ADD COLUMN reduced_animations INTEGER NOT NULL DEFAULT 0
            """
        }

        for column_name, migration_sql in migrations.items():
            if column_name not in existing_columns:
                connection.execute(migration_sql)

        # DIARY ENTRIES
        connection.execute("""
            CREATE TABLE IF NOT EXISTS diary_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # DIARY ENTRY MIGRATIONS (additive only - existing rows are kept)
        entry_columns = {
            column["name"]
            for column in connection.execute(
                "PRAGMA table_info(diary_entries)"
            ).fetchall()
        }

        if "is_favorite" not in entry_columns:
            connection.execute("""
                ALTER TABLE diary_entries
                ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0
            """)

        if "client_id" not in entry_columns:
            # Random id generated by the browser for entries written
            # offline. It makes syncing safe to repeat (idempotent).
            connection.execute("""
                ALTER TABLE diary_entries
                ADD COLUMN client_id TEXT
            """)

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_user_client
            ON diary_entries (user_id, client_id)
            WHERE client_id IS NOT NULL
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_user_created
            ON diary_entries (user_id, created_at)
        """)

        # TIME CAPSULES
        connection.execute("""
            CREATE TABLE IF NOT EXISTS time_capsules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                unlock_at TEXT NOT NULL,
                opened_at TEXT,

                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_capsules_user_unlock
            ON time_capsules (user_id, unlock_at)
        """)

        # PHOTO / AUDIO / VIDEO ATTACHMENTS
        # An attachment belongs either to a diary entry or to a
        # time capsule, never both.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS entry_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entry_id INTEGER,
                capsule_id INTEGER,
                kind TEXT NOT NULL
                    CHECK (kind IN ('photo', 'audio', 'video')),
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,

                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (entry_id)
                    REFERENCES diary_entries(id) ON DELETE CASCADE,
                FOREIGN KEY (capsule_id)
                    REFERENCES time_capsules(id) ON DELETE CASCADE,

                CHECK (
                    (entry_id IS NOT NULL) + (capsule_id IS NOT NULL) = 1
                )
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_entry
            ON entry_media (entry_id)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_capsule
            ON entry_media (capsule_id)
        """)

        # VOICE PROFILES (voice unlock)
        # Only a numeric voice template is stored - never raw audio.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS voice_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                template TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # MIGRATION TRACKING
        connection.execute("""
            CREATE TABLE IF NOT EXISTS app_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)

        # ONE-TIME MIGRATION:
        # Previous diary timestamps were saved in UTC by SQLite.
        # Convert them to Philippine local time (+8 hours).
        # This migration runs only once and preserves existing entries.

        migration_key = "diary_timestamps_to_philippine_time"

        migration_exists = connection.execute("""
            SELECT migration_key
            FROM app_migrations
            WHERE migration_key = ?
        """, (migration_key,)).fetchone()

        if migration_exists is None:

            entries = connection.execute("""
                SELECT id, created_at, updated_at
                FROM diary_entries
            """).fetchall()

            for entry in entries:

                converted_created_at = entry["created_at"]
                converted_updated_at = entry["updated_at"]

                try:
                    old_created_at = datetime.fromisoformat(
                        str(entry["created_at"])
                    )

                    if old_created_at.tzinfo is not None:
                        old_created_at = old_created_at.replace(
                            tzinfo=None
                        )

                    converted_created_at = (
                        old_created_at + timedelta(hours=8)
                    ).isoformat(sep=" ", timespec="seconds")

                except (ValueError, TypeError):
                    pass

                try:
                    old_updated_at = datetime.fromisoformat(
                        str(entry["updated_at"])
                    )

                    if old_updated_at.tzinfo is not None:
                        old_updated_at = old_updated_at.replace(
                            tzinfo=None
                        )

                    converted_updated_at = (
                        old_updated_at + timedelta(hours=8)
                    ).isoformat(sep=" ", timespec="seconds")

                except (ValueError, TypeError):
                    pass

                connection.execute("""
                    UPDATE diary_entries
                    SET created_at = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    converted_created_at,
                    converted_updated_at,
                    entry["id"]
                ))

            connection.execute("""
                INSERT INTO app_migrations (
                    migration_key,
                    applied_at
                )
                VALUES (?, ?)
            """, (
                migration_key,
                get_local_timestamp()
            ))

    # Write-ahead logging lets readers and a writer work at the same
    # time, which matters once several server workers are running.
    # It is stored in the database file, so it only needs setting once.
    try:
        connection = sqlite3.connect(DATABASE_PATH, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        finally:
            connection.close()
    except sqlite3.Error:
        # Not fatal: the default journal mode still works.
        pass


# =========================================
# LOGIN RATE LIMITING FUNCTIONS
# =========================================

def is_login_rate_limited(
    email,
    ip_address,
    max_attempts=5,
    window_seconds=900,
    max_ip_attempts=20
):
    """
    Check whether an email/IP combination or an IP address
    has exceeded the allowed number of failed login attempts.

    Defaults:
    - 5 attempts per email/IP combination
    - 20 attempts per IP address
    - 900 seconds (15 minutes)

    Returns True if login attempts should be blocked.
    """

    normalized_email = (email or "").strip().lower()
    normalized_ip = (ip_address or "unknown").strip()

    current_time = time.time()
    window_start = current_time - window_seconds

    with get_connection() as connection:

        email_ip_result = connection.execute("""
            SELECT COUNT(*) AS attempt_count
            FROM login_attempts
            WHERE email = ?
              AND ip_address = ?
              AND attempted_at >= ?
        """, (
            normalized_email,
            normalized_ip,
            window_start
        )).fetchone()

        ip_result = connection.execute("""
            SELECT COUNT(*) AS attempt_count
            FROM login_attempts
            WHERE ip_address = ?
              AND attempted_at >= ?
        """, (
            normalized_ip,
            window_start
        )).fetchone()

        email_ip_count = email_ip_result["attempt_count"]
        ip_count = ip_result["attempt_count"]

        return (
            email_ip_count >= max_attempts
            or ip_count >= max_ip_attempts
        )


def record_failed_login(email, ip_address):
    """
    Record a failed login attempt.

    Old records older than 24 hours are periodically removed.
    """

    normalized_email = (email or "").strip().lower()
    normalized_ip = (ip_address or "unknown").strip()

    current_time = time.time()
    cleanup_threshold = current_time - 86400

    with get_connection() as connection:

        connection.execute("""
            INSERT INTO login_attempts (
                email,
                ip_address,
                attempted_at
            )
            VALUES (?, ?, ?)
        """, (
            normalized_email,
            normalized_ip,
            current_time
        ))

        connection.execute("""
            DELETE FROM login_attempts
            WHERE attempted_at < ?
        """, (cleanup_threshold,))


def clear_login_attempts(email, ip_address):
    """
    Clear failed attempts for an email/IP combination
    after successful authentication.
    """

    normalized_email = (email or "").strip().lower()
    normalized_ip = (ip_address or "unknown").strip()

    with get_connection() as connection:

        connection.execute("""
            DELETE FROM login_attempts
            WHERE email = ?
              AND ip_address = ?
        """, (
            normalized_email,
            normalized_ip
        ))


# =========================================
# USER FUNCTIONS
# =========================================

def create_user(full_name, email, password_hash):
    with get_connection() as connection:

        cursor = connection.execute("""
            INSERT INTO users (
                full_name,
                email,
                password_hash
            )
            VALUES (?, ?, ?)
        """, (full_name, email, password_hash))

        user_id = cursor.lastrowid

        connection.execute("""
            INSERT INTO user_preferences (user_id)
            VALUES (?)
        """, (user_id,))

        return user_id


def get_user_by_email(email):
    with get_connection() as connection:

        user = connection.execute("""
            SELECT *
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()

        return user


def get_user_by_id(user_id):
    with get_connection() as connection:

        user = connection.execute("""
            SELECT
                id,
                full_name,
                email,
                session_version,
                created_at
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

        return user


def get_password_hash_by_id(user_id):
    """Return the stored password hash for a user id (or None)."""

    with get_connection() as connection:

        row = connection.execute("""
            SELECT password_hash
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

        return row["password_hash"] if row else None


def update_user_password_hash(user_id, password_hash):
    """
    Update a user's password hash and invalidate older sessions.

    Returns the new session version if successful.
    Returns None if the account does not exist.
    """

    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE users
            SET password_hash = ?,
                session_version = session_version + 1
            WHERE id = ?
        """, (
            password_hash,
            user_id
        ))

        if cursor.rowcount == 0:
            return None

        updated_user = connection.execute("""
            SELECT session_version
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

        if updated_user is None:
            return None

        return updated_user["session_version"]


# =========================================
# USER PREFERENCE FUNCTIONS
# =========================================

def get_user_preferences(user_id):
    with get_connection() as connection:

        connection.execute("""
            INSERT OR IGNORE INTO user_preferences (user_id)
            VALUES (?)
        """, (user_id,))

        preferences = connection.execute("""
            SELECT *
            FROM user_preferences
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        return preferences


def update_user_theme_mode(user_id, theme_mode):
    allowed_modes = ("light", "dark", "system")

    if theme_mode not in allowed_modes:
        return False

    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE user_preferences
            SET theme_mode = ?
            WHERE user_id = ?
        """, (theme_mode, user_id))

        return cursor.rowcount > 0


def update_user_personalization(
    user_id,
    seasonal_theme,
    font_style,
    text_size,
    reduced_animations
):
    allowed_themes = (
        "classic",
        "summer",
        "autumn",
        "winter",
        "spring"
    )

    allowed_fonts = (
        "modern",
        "serif",
        "rounded"
    )

    allowed_text_sizes = (
        "small",
        "medium",
        "large"
    )

    if seasonal_theme not in allowed_themes:
        return False

    if font_style not in allowed_fonts:
        return False

    if text_size not in allowed_text_sizes:
        return False

    if reduced_animations not in (True, False, 0, 1):
        return False

    reduced_animations = int(bool(reduced_animations))

    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE user_preferences
            SET seasonal_theme = ?,
                font_style = ?,
                text_size = ?,
                reduced_animations = ?
            WHERE user_id = ?
        """, (
            seasonal_theme,
            font_style,
            text_size,
            reduced_animations,
            user_id
        ))

        return cursor.rowcount > 0


# =========================================
# LEGACY APPEARANCE FUNCTION
# =========================================

def update_user_preferences(
    user_id,
    theme_name,
    background_color,
    accent_color
):
    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE user_preferences
            SET theme_name = ?,
                background_color = ?,
                accent_color = ?
            WHERE user_id = ?
        """, (
            theme_name,
            background_color,
            accent_color,
            user_id
        ))

        return cursor.rowcount > 0


# =========================================
# DIARY ENTRY FUNCTIONS
# =========================================

def create_entry(
    user_id,
    title,
    content,
    created_at=None,
    client_id=None
):
    """
    Create a diary entry and return its id.

    created_at  Optional Philippine-time timestamp string
                ("YYYY-MM-DD HH:MM:SS"). Used for entries that were
                written offline. Defaults to the current time.
    client_id   Optional browser-generated id that makes syncing an
                offline entry safe to repeat.
    """

    with get_connection() as connection:

        timestamp = get_local_timestamp()
        created_value = created_at or timestamp

        cursor = connection.execute("""
            INSERT INTO diary_entries (
                user_id,
                title,
                content,
                created_at,
                updated_at,
                client_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            title,
            content,
            created_value,
            timestamp,
            client_id
        ))

        return cursor.lastrowid


def get_entries_by_user(user_id):
    with get_connection() as connection:

        entries = connection.execute("""
            SELECT *
            FROM diary_entries
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
        """, (user_id,)).fetchall()

        return entries


def get_entry_by_id_and_user(entry_id, user_id):
    with get_connection() as connection:

        entry = connection.execute("""
            SELECT *
            FROM diary_entries
            WHERE id = ? AND user_id = ?
        """, (entry_id, user_id)).fetchone()

        return entry


def get_entry_by_client_id(user_id, client_id):
    """Find an entry previously created from an offline client id."""

    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM diary_entries
            WHERE user_id = ? AND client_id = ?
        """, (user_id, client_id)).fetchone()


def update_entry(entry_id, user_id, title, content):
    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE diary_entries
            SET title = ?,
                content = ?,
                updated_at = ?
            WHERE id = ? AND user_id = ?
        """, (
            title,
            content,
            get_local_timestamp(),
            entry_id,
            user_id
        ))

        return cursor.rowcount > 0


def delete_entry(entry_id, user_id):
    with get_connection() as connection:

        cursor = connection.execute("""
            DELETE FROM diary_entries
            WHERE id = ? AND user_id = ?
        """, (entry_id, user_id))

        return cursor.rowcount > 0


def delete_entry_returning_media(entry_id, user_id):
    """
    Delete a diary entry together with its attachment rows.

    Returns None when the entry does not exist (or belongs to someone
    else). Otherwise returns the list of stored file names so the
    caller can remove the files from disk.
    """

    with get_connection() as connection:

        entry = connection.execute("""
            SELECT id
            FROM diary_entries
            WHERE id = ? AND user_id = ?
        """, (entry_id, user_id)).fetchone()

        if entry is None:
            return None

        stored_names = [
            row["stored_name"]
            for row in connection.execute("""
                SELECT stored_name
                FROM entry_media
                WHERE entry_id = ? AND user_id = ?
            """, (entry_id, user_id)).fetchall()
        ]

        connection.execute("""
            DELETE FROM diary_entries
            WHERE id = ? AND user_id = ?
        """, (entry_id, user_id))

        return stored_names


def set_entry_favorite(entry_id, user_id, is_favorite):
    with get_connection() as connection:

        cursor = connection.execute("""
            UPDATE diary_entries
            SET is_favorite = ?
            WHERE id = ? AND user_id = ?
        """, (1 if is_favorite else 0, entry_id, user_id))

        return cursor.rowcount > 0


def count_entries_by_user(user_id):
    with get_connection() as connection:

        result = connection.execute("""
            SELECT COUNT(*)
            FROM diary_entries
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        return result[0]


def get_entry_stats(user_id):
    """
    Return the numbers shown on the dashboard:
    total entries, favorite entries and distinct days with a memory.
    """

    with get_connection() as connection:

        row = connection.execute("""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(is_favorite), 0) AS favorites,
                COUNT(DISTINCT substr(created_at, 1, 10)) AS days
            FROM diary_entries
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        return {
            "total": row["total"],
            "favorites": row["favorites"],
            "days": row["days"]
        }


def _like_pattern(term):
    escaped = (
        term.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
    )

    return f"%{escaped}%"


def get_entries_page(
    user_id,
    query="",
    favorites_only=False,
    page=1,
    per_page=10
):
    """
    Return (entries, total_matching) for one page of the diary list.

    Every word in the query must appear in the title or the text.
    """

    terms = [
        term for term in (query or "").split() if term
    ][:8]

    where = ["user_id = ?"]
    params = [user_id]

    if favorites_only:
        where.append("is_favorite = 1")

    for term in terms:
        pattern = _like_pattern(term)

        where.append(
            "(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')"
        )

        params.extend([pattern, pattern])

    where_sql = " AND ".join(where)

    page = max(1, int(page))
    per_page = max(1, int(per_page))

    with get_connection() as connection:

        total = connection.execute(
            f"SELECT COUNT(*) FROM diary_entries WHERE {where_sql}",
            params
        ).fetchone()[0]

        rows = connection.execute(
            f"""
            SELECT *
            FROM diary_entries
            WHERE {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, (page - 1) * per_page]
        ).fetchall()

        return rows, total


# =========================================
# MEDIA (PHOTO / AUDIO / VIDEO) FUNCTIONS
# =========================================

def create_media(
    user_id,
    kind,
    original_name,
    stored_name,
    mime_type,
    size_bytes,
    entry_id=None,
    capsule_id=None
):
    with get_connection() as connection:

        cursor = connection.execute("""
            INSERT INTO entry_media (
                user_id,
                entry_id,
                capsule_id,
                kind,
                original_name,
                stored_name,
                mime_type,
                size_bytes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            entry_id,
            capsule_id,
            kind,
            original_name,
            stored_name,
            mime_type,
            size_bytes,
            get_local_timestamp()
        ))

        return cursor.lastrowid


def get_media_by_id(media_id):
    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM entry_media
            WHERE id = ?
        """, (media_id,)).fetchone()


def list_media_for_entry(entry_id, user_id):
    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM entry_media
            WHERE entry_id = ? AND user_id = ?
            ORDER BY id
        """, (entry_id, user_id)).fetchall()


def list_media_for_capsule(capsule_id, user_id):
    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM entry_media
            WHERE capsule_id = ? AND user_id = ?
            ORDER BY id
        """, (capsule_id, user_id)).fetchall()


def count_media_for_entry(entry_id, user_id):
    with get_connection() as connection:

        return connection.execute("""
            SELECT COUNT(*)
            FROM entry_media
            WHERE entry_id = ? AND user_id = ?
        """, (entry_id, user_id)).fetchone()[0]


def count_media_for_capsule(capsule_id, user_id):
    with get_connection() as connection:

        return connection.execute("""
            SELECT COUNT(*)
            FROM entry_media
            WHERE capsule_id = ? AND user_id = ?
        """, (capsule_id, user_id)).fetchone()[0]


def get_media_summary_for_entries(user_id, entry_ids):
    """
    Return {entry_id: {"photo": n, "audio": n, "video": n}} so list
    pages can show small attachment badges without extra queries.
    """

    entry_ids = [int(entry_id) for entry_id in entry_ids]

    if not entry_ids:
        return {}

    placeholders = ",".join("?" for _ in entry_ids)

    with get_connection() as connection:

        rows = connection.execute(
            f"""
            SELECT entry_id, kind, COUNT(*) AS total
            FROM entry_media
            WHERE user_id = ?
              AND entry_id IN ({placeholders})
            GROUP BY entry_id, kind
            """,
            [user_id] + entry_ids
        ).fetchall()

    summary = {}

    for row in rows:
        summary.setdefault(row["entry_id"], {})[row["kind"]] = row["total"]

    return summary


def delete_media_row(media_id, user_id):
    """
    Delete one attachment row. Returns the deleted row (so the file
    can be removed from disk) or None if it was not found.
    """

    with get_connection() as connection:

        row = connection.execute("""
            SELECT *
            FROM entry_media
            WHERE id = ? AND user_id = ?
        """, (media_id, user_id)).fetchone()

        if row is None:
            return None

        connection.execute("""
            DELETE FROM entry_media
            WHERE id = ? AND user_id = ?
        """, (media_id, user_id))

        return row


# =========================================
# TIME CAPSULE FUNCTIONS
# =========================================

def create_capsule(user_id, title, content, unlock_at):
    with get_connection() as connection:

        cursor = connection.execute("""
            INSERT INTO time_capsules (
                user_id,
                title,
                content,
                created_at,
                unlock_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            title,
            content,
            get_local_timestamp(),
            unlock_at
        ))

        return cursor.lastrowid


def list_capsules(user_id):
    """
    Return capsule summaries. The message text is deliberately NOT
    selected here, so a sealed capsule's content can never leak
    through a list page.
    """

    with get_connection() as connection:

        return connection.execute("""
            SELECT
                id,
                user_id,
                title,
                created_at,
                unlock_at,
                opened_at
            FROM time_capsules
            WHERE user_id = ?
            ORDER BY unlock_at ASC, id ASC
        """, (user_id,)).fetchall()


def get_capsule_header(capsule_id, user_id):
    """Capsule details WITHOUT the message text."""

    with get_connection() as connection:

        return connection.execute("""
            SELECT
                id,
                user_id,
                title,
                created_at,
                unlock_at,
                opened_at
            FROM time_capsules
            WHERE id = ? AND user_id = ?
        """, (capsule_id, user_id)).fetchone()


def get_capsule_content(capsule_id, user_id):
    """
    Full capsule including the message. The caller must only use this
    after confirming the capsule is unlocked.
    """

    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM time_capsules
            WHERE id = ? AND user_id = ?
              AND unlock_at <= ?
        """, (
            capsule_id,
            user_id,
            get_local_timestamp()
        )).fetchone()


def mark_capsule_opened(capsule_id, user_id):
    with get_connection() as connection:

        connection.execute("""
            UPDATE time_capsules
            SET opened_at = ?
            WHERE id = ? AND user_id = ?
              AND opened_at IS NULL
              AND unlock_at <= ?
        """, (
            get_local_timestamp(),
            capsule_id,
            user_id,
            get_local_timestamp()
        ))


def delete_capsule_returning_media(capsule_id, user_id):
    """
    Delete a capsule and its attachment rows. Returns the list of
    stored file names to remove, or None when not found.
    """

    with get_connection() as connection:

        capsule = connection.execute("""
            SELECT id
            FROM time_capsules
            WHERE id = ? AND user_id = ?
        """, (capsule_id, user_id)).fetchone()

        if capsule is None:
            return None

        stored_names = [
            row["stored_name"]
            for row in connection.execute("""
                SELECT stored_name
                FROM entry_media
                WHERE capsule_id = ? AND user_id = ?
            """, (capsule_id, user_id)).fetchall()
        ]

        connection.execute("""
            DELETE FROM time_capsules
            WHERE id = ? AND user_id = ?
        """, (capsule_id, user_id))

        return stored_names


# =========================================
# VOICE PROFILE FUNCTIONS
# =========================================

def get_voice_profile(user_id):
    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM voice_profiles
            WHERE user_id = ?
        """, (user_id,)).fetchone()


def save_voice_profile(user_id, template_json, sample_count):
    timestamp = get_local_timestamp()

    with get_connection() as connection:

        connection.execute("""
            INSERT INTO voice_profiles (
                user_id,
                template,
                sample_count,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                template = excluded.template,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
        """, (
            user_id,
            template_json,
            sample_count,
            timestamp,
            timestamp
        ))


def delete_voice_profile(user_id):
    with get_connection() as connection:

        cursor = connection.execute("""
            DELETE FROM voice_profiles
            WHERE user_id = ?
        """, (user_id,))

        return cursor.rowcount > 0


# =========================================
# "ON THIS DAY" AND CAPSULE SUMMARY HELPERS
# =========================================

def get_entries_on_this_day(user_id, month_day, current_year):
    """
    Memories written on the same calendar day (MM-DD) in earlier years.
    """

    with get_connection() as connection:

        return connection.execute("""
            SELECT *
            FROM diary_entries
            WHERE user_id = ?
              AND substr(created_at, 6, 5) = ?
              AND CAST(substr(created_at, 1, 4) AS INTEGER) < ?
            ORDER BY created_at DESC, id DESC
        """, (user_id, month_day, int(current_year))).fetchall()


def count_ready_capsules(user_id):
    """Capsules whose opening time has passed but were never opened."""

    with get_connection() as connection:

        return connection.execute("""
            SELECT COUNT(*)
            FROM time_capsules
            WHERE user_id = ?
              AND unlock_at <= ?
              AND opened_at IS NULL
        """, (user_id, get_local_timestamp())).fetchone()[0]
