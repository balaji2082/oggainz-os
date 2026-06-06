"""
Automatic, safe SQLite backups.

Uses SQLite's online-backup API so a copy is consistent even while the app is
running (and even with WAL mode). Keeps the most recent KEEP files.
"""

import os
import sqlite3
import glob
from datetime import datetime

import db

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(APP_DIR, "backups")
KEEP = 30


def make_backup(quiet=False):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"oggainz-{stamp}.db")

    src = sqlite3.connect(db.DB_PATH)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    _prune()
    if not quiet:
        print(f"Backup written: {dest}")
    return dest


def _prune():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "oggainz-*.db")))
    for old in files[:-KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass


if __name__ == "__main__":
    make_backup()
