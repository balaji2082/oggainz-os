"""
OG Gainz Command Center — pull the LIVE database down to this laptop.

One-way sync: PythonAnywhere (source of truth) -> local oggainz.db.
Never the other direction — this script only ever reads from the server.

Setup (once):
  1. https://www.pythonanywhere.com/account/#api_token  -> click "Create a new API token".
  2. Paste that token into a new file  OG-Gainz-OS/.pa_token  (just the token, nothing else).
     That file is gitignored — it never gets committed or pushed.

Run it any time you want a fresh local copy (before testing, or just as a backup):
  python pull_live_db.py

What it does:
  * Downloads the live oggainz.db from PythonAnywhere over their Files API.
  * Verifies it's really a SQLite file before touching anything (a bad token or
    wrong path returns an error page/JSON, not a database — this catches that).
  * Backs up your CURRENT local oggainz.db first (backups/pre-pull-<timestamp>.db),
    so a bad pull is always reversible.
  * Replaces oggainz.db and clears any stale -wal/-shm sidecar files from the old copy.
"""

import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PA_USERNAME = "oggainz"                       # your PythonAnywhere username
REMOTE_DB_PATH = f"/home/{PA_USERNAME}/oggainz-os/oggainz.db"
TOKEN_FILE = os.path.join(APP_DIR, ".pa_token")
DB_PATH = os.path.join(APP_DIR, "oggainz.db")
BACKUP_DIR = os.path.join(APP_DIR, "backups")


def get_token():
    token = os.environ.get("OG_PA_TOKEN")
    if token:
        return token.strip()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    print("No API token found.\n\n"
          "1. Go to https://www.pythonanywhere.com/account/#api_token\n"
          "   and click 'Create a new API token' (or copy your existing one).\n"
          f"2. Paste just the token into a new file:\n   {TOKEN_FILE}\n")
    sys.exit(1)


def download(token):
    url = (f"https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}"
           f"/files/path{REMOTE_DB_PATH}/")
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            print("PythonAnywhere rejected the token (401 Unauthorized).\n"
                  f"Check the token in {TOKEN_FILE} is correct and not expired.")
        elif e.code == 404:
            print(f"File not found on the server (404): {REMOTE_DB_PATH}\n"
                  "If your PythonAnywhere username or repo folder name is different, "
                  "edit PA_USERNAME / REMOTE_DB_PATH at the top of this script.")
        else:
            print(f"PythonAnywhere returned HTTP {e.code}:\n{body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Could not reach PythonAnywhere ({e.reason}). Check your internet connection.")
        sys.exit(1)


def main():
    token = get_token()
    print(f"Downloading live oggainz.db from PythonAnywhere ({PA_USERNAME})…")
    data = download(token)

    if not data.startswith(b"SQLite format 3\x00"):
        print("Download did not look like a SQLite database (got "
              f"{len(data)} bytes starting with {data[:40]!r}).\n"
              "Nothing was changed locally. Check the token / path and try again.")
        sys.exit(1)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"pre-pull-{stamp}.db")
        shutil.copy2(DB_PATH, backup_path)
        print(f"Backed up your current local copy -> {os.path.relpath(backup_path, APP_DIR)}")

    with open(DB_PATH, "wb") as f:
        f.write(data)
    for ext in ("-wal", "-shm"):                 # stale sidecar files from the OLD db
        stale = DB_PATH + ext
        if os.path.exists(stale):
            os.remove(stale)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        customers = conn.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
        active = conn.execute(
            "SELECT COUNT(*) n FROM subscriptions WHERE status IN ('Active','Ending Soon')"
        ).fetchone()["n"]
        total_subs = conn.execute("SELECT COUNT(*) n FROM subscriptions").fetchone()["n"]
        print(f"\nDone — local oggainz.db now matches the live server:")
        print(f"  {customers} customers · {total_subs} subscriptions ({active} active)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
