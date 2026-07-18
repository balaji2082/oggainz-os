"""
Sync the social-media pipeline files into the Content dashboard.

Reads the post cards in  Knowledge/Business/OG-GAINZ/Social-Media/_briefs/
and the shoot plans in   .../_shoots/ , then:
  1. upserts them into the local oggainz.db (always), and
  2. if OG_SYNC_URL is set (e.g. https://oggainz.pythonanywhere.com), POSTs the
     same payload to <url>/content/api/sync with OG_SYNC_TOKEN so the LIVE
     dashboard matches too.

Run it any time (the production-manager agent runs it after pipeline changes):
    python sync_content.py
"""

import json
import os
import re
import sys
import urllib.request
from datetime import date

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SOCIAL = os.path.normpath(os.path.join(
    APP_DIR, "..", "Knowledge", "Business", "OG-GAINZ", "Social-Media"))
BRIEFS = os.path.join(SOCIAL, "_briefs")
SHOOTS = os.path.join(SOCIAL, "_shoots")

VALID_STATUSES = {"IDEA", "BRIEF", "RESEARCHED", "SCRIPTED", "DRAFTED", "SHOT",
                  "EDITING", "DESIGNED", "REVIEW", "APPROVED", "SCHEDULED",
                  "PUBLISHED", "KILLED"}


def _field(text, name):
    """Grab a `name: value` line (template style), ignoring inline # comments."""
    m = re.search(rf"^{name}\s*:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    return m.group(1).split("#")[0].strip() or None


def parse_card(path):
    name = os.path.basename(path)
    m = re.match(r"([RP]-\d+|D\d+)", name)
    if not m:
        return None                              # not a post card (e.g. _WEEK plan)
    text = open(path, encoding="utf-8", errors="replace").read()
    pid = m.group(1)

    status = (_field(text, "status") or "BRIEF").upper()
    if status not in VALID_STATUSES:
        status = "BRIEF"

    # date line looks like: `date: 2026-08-03  time: 18:30 IST`
    post_date = None
    dm = re.search(r"^date\s*:\s*(\d{4}-\d{2}-\d{2})", text, re.M | re.I)
    if dm:
        post_date = dm.group(1)

    # hook: first line following a Hook heading/bold marker
    hook = None
    hm = re.search(r"\*\*Hook\*\*[^\n]*[:—-]\s*(.+)", text) or \
         re.search(r"^##\s*Hook[^\n]*\n+[-*]?\s*(.+)", text, re.M)
    if hm:
        hook = hm.group(1).strip().strip('"“”')[:200]

    # title from the filename slug: R-012-brand-reel-protein-myths.md
    parts = name.rsplit(".", 1)[0].split("-")
    slug = "-".join(parts[4:]) if len(parts) > 4 else "-".join(parts[1:])
    title = slug.replace("-", " ").strip().title() or pid

    return {
        "id": pid, "title": title,
        "account": (_field(text, "account") or "brand").split("|")[0].strip(),
        "format": (_field(text, "format") or "reel").split("|")[0].strip(),
        "pillar": _field(text, "pillar"),
        "status": status,
        "editor": (_field(text, "editor") or "").replace("<unassigned", "").strip("<>| ") or None,
        "due_date": _field(text, "due"),
        "post_date": post_date,
        "hook": hook,
        "body": text[:6000],
        "file": os.path.relpath(path, SOCIAL).replace("\\", "/"),
        "notes": None,
    }


def parse_shoot(path):
    name = os.path.basename(path).rsplit(".", 1)[0]
    dm = re.match(r"(\d{4}-\d{2}-\d{2})", name)
    if not dm:
        return None
    text = open(path, encoding="utf-8", errors="replace").read()
    tm = re.search(r"^#\s*(.+)$", text, re.M)
    status = (_field(text, "status") or "").upper()
    if status not in ("PLANNED", "DONE", "CANCELLED"):
        status = "DONE" if dm.group(1) < date.today().isoformat() else "PLANNED"
    return {"id": name, "date": dm.group(1),
            "title": tm.group(1).strip() if tm else name,
            "status": status, "notes": None,
            "file": os.path.relpath(path, SOCIAL).replace("\\", "/")}


def collect():
    posts, shoots = [], []
    if os.path.isdir(BRIEFS):
        for f in sorted(os.listdir(BRIEFS)):
            if f.endswith(".md") and not f.startswith("_"):
                card = parse_card(os.path.join(BRIEFS, f))
                if card:
                    posts.append(card)
    if os.path.isdir(SHOOTS):
        for f in sorted(os.listdir(SHOOTS)):
            if f.endswith(".md") and not f.startswith("_"):
                s = parse_shoot(os.path.join(SHOOTS, f))
                if s:
                    shoots.append(s)
    return posts, shoots


def sync_local(posts, shoots):
    """Upsert straight into the local DB via the same code the API uses."""
    import content_hub
    content_hub.ensure_schema()
    import db
    c = db.get_db()
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for p in posts:
        c.execute(
            "INSERT INTO content_posts (id,title,account,format,pillar,status,"
            " editor,due_date,post_date,hook,body,file,notes,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
            " account=excluded.account, format=excluded.format,"
            " pillar=excluded.pillar, status=excluded.status,"
            " editor=excluded.editor, due_date=excluded.due_date,"
            " post_date=excluded.post_date, hook=excluded.hook,"
            " body=excluded.body, file=excluded.file, updated_at=excluded.updated_at",
            (p["id"], p["title"], p["account"], p["format"], p["pillar"],
             p["status"], p["editor"], p["due_date"], p["post_date"],
             p["hook"], p["body"], p["file"], p["notes"], now))
    for s in shoots:
        c.execute(
            "INSERT INTO content_shoots (id,date,title,status,notes,file) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET date=excluded.date,"
            " title=excluded.title, status=excluded.status, file=excluded.file",
            (s["id"], s["date"], s["title"], s["status"], s["notes"], s["file"]))
    c.commit()
    c.close()


def sync_remote(posts, shoots):
    url = os.environ.get("OG_SYNC_URL")
    if not url:
        return "remote: skipped (OG_SYNC_URL not set)"
    token = os.environ.get("OG_SYNC_TOKEN", "og-sync-local")
    payload = json.dumps({"token": token, "posts": posts,
                          "shoots": shoots}).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/content/api/sync", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return "remote: " + r.read().decode()
    except Exception as e:                       # report, never crash the sync
        return f"remote: FAILED ({e})"


if __name__ == "__main__":
    posts, shoots = collect()
    sync_local(posts, shoots)
    print(f"local: synced {len(posts)} posts, {len(shoots)} shoots -> oggainz.db")
    print(sync_remote(posts, shoots))
    if not posts:
        print("note: _briefs/ has no post cards yet - run /og-content to plan the week.")
    sys.exit(0)
