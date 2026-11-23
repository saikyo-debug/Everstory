"""
Back up Everstory: a consistent copy of the database plus (optionally)
all uploaded photos, voice notes and videos.

    python scripts/backup_db.py                 # backups go to <data folder>/backups
    python scripts/backup_db.py --dest D:\\Backups --keep 30
    python scripts/backup_db.py --no-uploads    # database only

It is safe to run while the app is running (it uses SQLite's own backup
mechanism, so the copy is never half-written). Schedule it daily with
Task Scheduler (Windows) or cron (Linux), and copy the backups somewhere
that is NOT the same disk as the app.

To restore: stop the app, copy a backup .db file over alaala.db, unpack
the matching uploads archive into the uploads folder, start the app.
"""

import argparse
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


def prune(directory, pattern, keep):
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)

    for old in files[:-keep] if keep > 0 else []:
        old.unlink()
        print(f"  removed old backup {old.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, default=config.DATA_DIR / "backups")
    parser.add_argument("--keep", type=int, default=14,
                        help="how many backups of each kind to keep (default 14)")
    parser.add_argument("--no-uploads", action="store_true",
                        help="back up the database only")
    args = parser.parse_args()

    database_path = config.DEFAULT_DATABASE_PATH
    uploads_path = config.DEFAULT_UPLOAD_DIR

    if not database_path.is_file():
        sys.exit(f"Database not found: {database_path}")

    args.dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    target = args.dest / f"alaala-{stamp}.db"

    source = sqlite3.connect(database_path)
    copy = sqlite3.connect(target)

    try:
        source.backup(copy)
        result = copy.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        copy.close()
        source.close()

    if result != "ok":
        target.unlink(missing_ok=True)
        sys.exit(f"Backup failed its integrity check: {result}")

    print(f"Database backed up: {target}")

    if not args.no_uploads and uploads_path.is_dir():
        archive = args.dest / f"uploads-{stamp}.tar.gz"

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(uploads_path, arcname="uploads")

        print(f"Uploads backed up:  {archive}")

    prune(args.dest, "alaala-*.db", args.keep)
    prune(args.dest, "uploads-*.tar.gz", args.keep)


if __name__ == "__main__":
    main()
