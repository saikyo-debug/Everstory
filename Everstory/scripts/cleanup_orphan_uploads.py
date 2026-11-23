"""
Find (and optionally delete) uploaded files that no memory or time
capsule refers to any more. They can be left behind if the server is
stopped in the middle of deleting something.

    python scripts/cleanup_orphan_uploads.py            # only LISTS them
    python scripts/cleanup_orphan_uploads.py --delete   # removes them

Back up first (scripts/backup_db.py) if you are unsure.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402

STORED_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()

    uploads = config.DEFAULT_UPLOAD_DIR

    if not uploads.is_dir():
        print("No uploads folder yet. Nothing to do.")
        return

    connection = sqlite3.connect(config.DEFAULT_DATABASE_PATH)

    try:
        known = {
            row[0] for row in connection.execute(
                "SELECT stored_name FROM entry_media"
            )
        }
    finally:
        connection.close()

    orphans = [
        path for path in uploads.glob("*/*")
        if path.is_file()
        and STORED_NAME.match(path.name)
        and path.name not in known
    ]

    if not orphans:
        print("No orphaned files found.")
        return

    total = sum(path.stat().st_size for path in orphans)

    for path in orphans:
        print(("Deleting " if args.delete else "Orphan:   ") + str(path))

        if args.delete:
            path.unlink()

    print(f"\n{len(orphans)} file(s), {total / 1024 / 1024:.1f} MB"
          + ("" if args.delete else "  (run again with --delete to remove)"))


if __name__ == "__main__":
    main()
