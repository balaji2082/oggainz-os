"""
OG Gainz Command Center — Content Hub (the social-media production dashboard).

A self-contained module: the content board (reels + posts moving through the
pipeline), shoot days, the task list, and a token-protected sync API that the
Claude agent team (sync_content.py) pushes card statuses into.

Roles:
  admin  — Balaji. Full control: any status, assign editors, tasks, approvals.
  team   — editor friends (log in with OG_TEAM_PASSWORD). They see ONLY the
           content pages, can pick up / deliver their own assigned reels
           (SHOT→EDITING→REVIEW), comment, and tick their own tasks. Every
           other Command Center page stays admin-only.

The markdown post cards in Knowledge/.../Social-Media/_briefs/ remain the
source of truth for CONTENT; this dashboard is the source of truth for
STATUS VISIBILITY. sync_content.py keeps the two matching.
"""

from datetime import date, datetime, timedelta

from flask import (abort, jsonify, redirect, render_template, request,
                   session, url_for)

import db

# Pipeline definition -------------------------------------------------------
REEL_LANE = ["IDEA", "BRIEF", "RESEARCHED", "SCRIPTED", "SHOT", "EDITING",
             "REVIEW", "APPROVED", "SCHEDULED", "PUBLISHED"]
POST_LANE = ["IDEA", "BRIEF", "RESEARCHED", "DRAFTED", "DESIGNED",
             "REVIEW", "APPROVED", "SCHEDULED", "PUBLISHED"]
ALL_STATUSES = ["IDEA", "BRIEF", "RESEARCHED", "SCRIPTED", "DRAFTED", "SHOT",
                "EDITING", "DESIGNED", "REVIEW", "APPROVED", "SCHEDULED",
                "PUBLISHED", "KILLED"]

# Board columns: group statuses so the kanban stays phone-sized.
BOARD_COLS = [
    ("Planned",    ["IDEA", "BRIEF", "RESEARCHED"]),
    ("Written",    ["SCRIPTED", "DRAFTED"]),
    ("Production", ["SHOT", "EDITING", "DESIGNED"]),
    ("Review",     ["REVIEW"]),
    ("Ready",      ["APPROVED", "SCHEDULED"]),
    ("Published",  ["PUBLISHED"]),
]

# Transitions a TEAM member may perform on a card assigned to them:
#   pick up an edit, deliver a draft for review.
TEAM_MOVES = {"SHOT": ["EDITING"], "EDITING": ["REVIEW"]}

QUEUE_FLOOR = 7          # days of ready content the queue must always hold
WEEK_REEL_TARGET = 5
WEEK_POST_TARGET = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS content_posts (
    id         TEXT PRIMARY KEY,      -- R-001 / P-001
    title      TEXT,
    account    TEXT,                  -- brand | personal
    format     TEXT,                  -- reel | carousel | static | story
    pillar     TEXT,
    status     TEXT DEFAULT 'BRIEF',
    editor     TEXT,                  -- assigned human editor (reels)
    due_date   TEXT,                  -- editor draft deadline
    post_date  TEXT,                  -- planned publish date
    hook       TEXT,
    body       TEXT,                  -- excerpt of the markdown card (process visibility)
    file       TEXT,                  -- path of the source card on the laptop
    notes      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS content_activity (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT,
    who     TEXT,
    action  TEXT,                     -- status | comment | assign | sync
    note    TEXT,
    at      TEXT
);

CREATE TABLE IF NOT EXISTS content_shoots (
    id     TEXT PRIMARY KEY,          -- 2026-07-19-Shoot-01
    date   TEXT,
    title  TEXT,
    status TEXT DEFAULT 'PLANNED',    -- PLANNED | DONE | CANCELLED
    notes  TEXT,
    file   TEXT
);

CREATE TABLE IF NOT EXISTS content_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    owner      TEXT DEFAULT 'Balaji', -- Balaji or an editor's name
    due        TEXT,
    done       INTEGER DEFAULT 0,
    created_at TEXT
);
"""


def ensure_schema():
    c = db.get_db()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def role():
    """admin | team. Local mode (no login configured) is always admin."""
    return session.get("role") or "admin"


def actor():
    """Who is doing this action, for the activity log."""
    if role() == "admin":
        return "Balaji"
    return session.get("team_name") or "team"


def log(c, post_id, action, note):
    c.execute("INSERT INTO content_activity (post_id, who, action, note, at) "
              "VALUES (?,?,?,?,?)", (post_id, actor(), action, note, now_iso()))


def lane_for(fmt):
    return REEL_LANE if (fmt or "").lower() == "reel" else POST_LANE


def week_bounds(d=None):
    d = d or date.today()
    mon = d - timedelta(days=d.weekday())
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


def board_stats(c):
    rows = c.execute("SELECT * FROM content_posts WHERE status != 'KILLED'").fetchall()
    ready = [r for r in rows if r["status"] in ("APPROVED", "SCHEDULED")]
    mon, sun = week_bounds()
    wk = [r for r in rows if r["post_date"] and mon <= r["post_date"] <= sun
          and r["status"] in ("APPROVED", "SCHEDULED", "PUBLISHED")]
    wk_reels = sum(1 for r in wk if (r["format"] or "") == "reel")
    wk_posts = len(wk) - wk_reels
    today = date.today().isoformat()
    overdue = [r for r in rows if r["status"] == "EDITING" and r["due_date"]
               and r["due_date"] < today]
    return {"queue_days": len(ready), "queue_floor": QUEUE_FLOOR,
            "wk_reels": wk_reels, "wk_reel_target": WEEK_REEL_TARGET,
            "wk_posts": wk_posts, "wk_post_target": WEEK_POST_TARGET,
            "overdue": overdue}


# ---------------------------------------------------------------------------
# Route registration (called once from app.py)
# ---------------------------------------------------------------------------
def register(app):
    ensure_schema()

    # Pages a team login may reach. Everything else bounces to the board.
    team_ok = {"content_board", "content_post", "content_post_status",
               "content_post_comment", "content_shoots", "content_tasks",
               "content_task_toggle", "content_set_name", "content_sync_api",
               "login", "logout", "static"}

    @app.before_request
    def _content_team_gate():
        if session.get("role") == "team" and request.endpoint not in team_ok:
            return redirect(url_for("content_board"))

    @app.context_processor
    def _content_globals():
        return {"c_role": role(), "c_name": actor(),
                "c_is_admin": role() == "admin"}

    # -- Board --------------------------------------------------------------
    @app.route("/content")
    def content_board():
        c = db.get_db()
        rows = c.execute("SELECT * FROM content_posts WHERE status != 'KILLED' "
                         "ORDER BY COALESCE(post_date, due_date, '9999'), id").fetchall()
        cols = [(name, [r for r in rows if r["status"] in group])
                for name, group in BOARD_COLS]
        stats = board_stats(c)
        c.close()
        return render_template("content_board.html", cols=cols, stats=stats)

    # -- Card detail --------------------------------------------------------
    @app.route("/content/post/<pid>")
    def content_post(pid):
        c = db.get_db()
        p = c.execute("SELECT * FROM content_posts WHERE id=?", (pid,)).fetchone()
        if not p:
            c.close()
            abort(404)
        acts = c.execute("SELECT * FROM content_activity WHERE post_id=? "
                         "ORDER BY id DESC LIMIT 40", (pid,)).fetchall()
        c.close()
        lane = lane_for(p["format"])
        mine = role() == "team" and (p["editor"] or "").strip().lower() == actor().strip().lower()
        moves = (lane if role() == "admin"
                 else TEAM_MOVES.get(p["status"], []) if mine else [])
        return render_template("content_post.html", p=p, acts=acts, lane=lane,
                               moves=moves, mine=mine)

    @app.route("/content/post/<pid>/status", methods=["POST"])
    def content_post_status(pid):
        new = (request.form.get("status") or "").upper()
        c = db.get_db()
        p = c.execute("SELECT * FROM content_posts WHERE id=?", (pid,)).fetchone()
        if not p or new not in ALL_STATUSES:
            c.close()
            abort(400)
        if role() != "admin":
            mine = (p["editor"] or "").strip().lower() == actor().strip().lower()
            if not mine or new not in TEAM_MOVES.get(p["status"], []):
                c.close()
                abort(403)                    # team: only their own allowed moves
        c.execute("UPDATE content_posts SET status=?, updated_at=? WHERE id=?",
                  (new, now_iso(), pid))
        log(c, pid, "status", f"{p['status']} → {new}")
        c.commit()
        c.close()
        return redirect(url_for("content_post", pid=pid))

    @app.route("/content/post/<pid>/comment", methods=["POST"])
    def content_post_comment(pid):
        note = (request.form.get("note") or "").strip()
        if note:
            c = db.get_db()
            log(c, pid, "comment", note[:500])
            c.commit()
            c.close()
        return redirect(url_for("content_post", pid=pid))

    @app.route("/content/post/<pid>/assign", methods=["POST"])
    def content_post_assign(pid):
        if role() != "admin":
            abort(403)
        editor = (request.form.get("editor") or "").strip()
        due = (request.form.get("due") or "").strip() or None
        c = db.get_db()
        c.execute("UPDATE content_posts SET editor=?, due_date=?, updated_at=? "
                  "WHERE id=?", (editor or None, due, now_iso(), pid))
        log(c, pid, "assign", f"editor = {editor or '—'}, due {due or '—'}")
        c.commit()
        c.close()
        return redirect(url_for("content_post", pid=pid))

    # -- Team member picks their display name (once per login) --------------
    @app.route("/content/name", methods=["POST"])
    def content_set_name():
        name = (request.form.get("name") or "").strip()[:40]
        if name:
            session["team_name"] = name
        return redirect(request.form.get("back") or url_for("content_board"))

    # -- Shoots -------------------------------------------------------------
    @app.route("/content/shoots")
    def content_shoots():
        c = db.get_db()
        shoots = c.execute("SELECT * FROM content_shoots ORDER BY date DESC").fetchall()
        c.close()
        return render_template("content_shoots.html", shoots=shoots)

    # -- Tasks --------------------------------------------------------------
    @app.route("/content/tasks", methods=["GET", "POST"])
    def content_tasks():
        c = db.get_db()
        if request.method == "POST":
            if role() != "admin":
                c.close()
                abort(403)
            title = (request.form.get("title") or "").strip()
            if title:
                c.execute("INSERT INTO content_tasks (title, owner, due, created_at) "
                          "VALUES (?,?,?,?)",
                          (title, (request.form.get("owner") or "Balaji").strip(),
                           (request.form.get("due") or "").strip() or None, now_iso()))
                c.commit()
            c.close()
            return redirect(url_for("content_tasks"))
        tasks = c.execute("SELECT * FROM content_tasks "
                          "ORDER BY done, COALESCE(due,'9999'), id").fetchall()
        c.close()
        return render_template("content_tasks.html", tasks=tasks)

    @app.route("/content/tasks/<int:tid>/toggle", methods=["POST"])
    def content_task_toggle(tid):
        c = db.get_db()
        t = c.execute("SELECT * FROM content_tasks WHERE id=?", (tid,)).fetchone()
        if not t:
            c.close()
            abort(404)
        if role() != "admin" and (t["owner"] or "").strip().lower() != actor().strip().lower():
            c.close()
            abort(403)                        # team: tick only your own tasks
        c.execute("UPDATE content_tasks SET done=? WHERE id=?",
                  (0 if t["done"] else 1, tid))
        c.commit()
        c.close()
        return redirect(url_for("content_tasks"))

    # -- Sync API (Claude's sync_content.py posts here) ----------------------
    # Token-protected instead of session-protected, so the laptop script can
    # push to the live server. Endpoint is in PUBLIC_ENDPOINTS in app.py.
    @app.route("/content/api/sync", methods=["POST"])
    def content_sync_api():
        import os
        token = os.environ.get("OG_SYNC_TOKEN", "og-sync-local")
        data = request.get_json(silent=True) or {}
        if data.get("token") != token:
            return jsonify({"ok": False, "error": "bad token"}), 403
        c = db.get_db()
        n_posts = n_shoots = 0
        for p in data.get("posts", []):
            if not p.get("id"):
                continue
            old = c.execute("SELECT status FROM content_posts WHERE id=?",
                            (p["id"],)).fetchone()
            c.execute(
                "INSERT INTO content_posts (id,title,account,format,pillar,status,"
                " editor,due_date,post_date,hook,body,file,notes,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                " account=excluded.account, format=excluded.format,"
                " pillar=excluded.pillar, status=excluded.status,"
                " editor=excluded.editor, due_date=excluded.due_date,"
                " post_date=excluded.post_date, hook=excluded.hook,"
                " body=excluded.body, file=excluded.file, notes=excluded.notes,"
                " updated_at=excluded.updated_at",
                (p["id"], p.get("title"), p.get("account"), p.get("format"),
                 p.get("pillar"), (p.get("status") or "BRIEF").upper(),
                 p.get("editor"), p.get("due_date"), p.get("post_date"),
                 p.get("hook"), (p.get("body") or "")[:6000], p.get("file"),
                 p.get("notes"), now_iso()))
            if old and old["status"] != (p.get("status") or "BRIEF").upper():
                c.execute("INSERT INTO content_activity (post_id, who, action, note, at) "
                          "VALUES (?,?,?,?,?)",
                          (p["id"], "agent-team", "sync",
                           f"{old['status']} → {(p.get('status') or 'BRIEF').upper()}",
                           now_iso()))
            n_posts += 1
        for s in data.get("shoots", []):
            if not s.get("id"):
                continue
            c.execute(
                "INSERT INTO content_shoots (id,date,title,status,notes,file) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET date=excluded.date,"
                " title=excluded.title, status=excluded.status,"
                " notes=excluded.notes, file=excluded.file",
                (s["id"], s.get("date"), s.get("title"),
                 (s.get("status") or "PLANNED").upper(), s.get("notes"),
                 s.get("file")))
            n_shoots += 1
        c.commit()
        c.close()
        return jsonify({"ok": True, "posts": n_posts, "shoots": n_shoots})
