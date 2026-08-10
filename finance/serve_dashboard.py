#!/usr/bin/env python3
"""
Editable finance dashboard — tiny local server.

Why this exists: a dashboard opened as a file:// page CANNOT write to Notion. Notion's
API sends no CORS headers for browser origins, so the fetch is blocked outright, and a
page holding the integration token would be a secret sitting in a file anyone could open.
So the page talks to this server on localhost, and this server — which already has the
token — talks to Notion.

Run it with:  Edit-Dashboard.bat   (or:  python finance/serve_dashboard.py)

Stop it with Ctrl+C, or just close the window. While it is NOT running, dashboard.html
still opens normally as a plain file — it just hides the editing controls.

Listens on the local network as well as localhost, so a phone on the same wifi can use it.
There is no password: anyone on your wifi who knows the address can read and change your
finance rows while this is running. On home wifi that's fine. On shared or public wifi,
start it with  --local-only .
"""

import argparse
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_finance as bf  # noqa: E402

API = "https://api.notion.com/v1"
PORT = 8770

# client field name -> (Notion property name, kind), per database
FIELD_MAP = {
    "business": {
        "what": ("What", "title"),
        "date": ("Date", "date"),
        "amount": ("Amount", "number"),
        "category": ("Category", "select"),
        "subcategory": ("Subcategory", "select"),
        "vendor": ("Vendor", "text"),
        "payment": ("Payment Method", "select"),
        "paid_from": ("Paid From", "select"),
        "spread": ("Spread Over Months", "number"),
        "orders_covered": ("Orders Covered", "number"),
        "notes": ("Notes", "text"),
    },
    "personal": {
        "what": ("What", "title"),
        "date": ("Date", "date"),
        "amount": ("Amount", "number"),
        "category": ("Category", "select"),
        "subcategory": ("Subcategory", "select"),
        "payment": ("Payment Method", "select"),
        "notes": ("Notes", "text"),
    },
    "dad": {
        "what": ("What", "title"),
        "date": ("Date", "date"),
        "amount": ("Amount", "number"),
        "category": ("Category", "select"),
        "to_business": ("Goes To Business", "yesno"),
        "notes": ("Notes", "text"),
    },
}

STATE = {"token": None, "db_ids": {}, "schema": {}}

# Which select properties each sheet actually has, and the label shown in the UI.
LIST_PROPS = {
    "business": [("Category", "Category"), ("Subcategory", "Subcategory"),
                 ("Payment Method", "Payment method"), ("Paid From", "Paid from")],
    "personal": [("Category", "Category"), ("Subcategory", "Subcategory"),
                 ("Payment Method", "Payment method")],
    "dad": [("Category", "Category")],
}
# Business categories must start with one of these, because the Cost Type formula in
# Notion derives the class by taking everything before " · ". A category without a
# prefix silently invents a new cost type and breaks the COGS/OPEX/CAPEX split.
COST_CLASSES = ["COGS", "OPEX", "CAPEX", "DRAW"]
SEP = " · "

PAIRS_FILE = os.path.join(bf.DATA_DIR, "pairs.json")
BACKUP_FILE = os.path.join(bf.DATA_DIR, "schema_backup.json")


# ---------------------------------------------------------------- option lists

def current_options(db_key, prop):
    """Live [{id, name}] for one select property."""
    db = notion("GET", f"/databases/{STATE['db_ids'][db_key]}")
    p = db.get("properties", {}).get(prop)
    if not p or p.get("type") != "select":
        raise ValueError(f"{prop!r} isn't a dropdown on the {db_key} sheet")
    return [{"id": o["id"], "name": o["name"]} for o in p["select"]["options"]]


def set_options(db_key, prop, ordered_names):
    """Replace a select's options with exactly `ordered_names`.

    *** The whole list, every time. ***  Notion treats this PATCH as a replacement: any
    existing option left out is DELETED, and deleting an option silently blanks that
    value on every row using it. Existing options are sent by id so they keep their
    colour and their link to existing rows; only genuinely new names are sent by name.
    This helper is the only place options are written — never PATCH a partial list.
    """
    by_name = {o["name"]: o["id"] for o in current_options(db_key, prop)}
    payload = [({"id": by_name[n]} if n in by_name else {"name": n})
               for n in ordered_names]
    res = notion("PATCH", f"/databases/{STATE['db_ids'][db_key]}",
                 {"properties": {prop: {"select": {"options": payload}}}})
    return [o["name"] for o in res["properties"][prop]["select"]["options"]]


def all_rows(db_key):
    """Every page in a database, paged."""
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        payload = notion("POST", f"/databases/{STATE['db_ids'][db_key]}/query", body)
        out.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
    return out


def usage_counts(db_key, prop):
    """How many rows use each option — what makes deleting safe to reason about."""
    counts = {}
    for pg in all_rows(db_key):
        v = (pg.get("properties", {}).get(prop, {}).get("select") or {}).get("name")
        if v:
            counts[v] = counts.get(v, 0) + 1
    return counts


def rows_using(db_key, prop, name):
    return [pg["id"] for pg in all_rows(db_key)
            if (pg.get("properties", {}).get(prop, {}).get("select") or {}).get("name") == name]


def snapshot(db_key, prop, why):
    """Write a restore point before anything destructive.

    Deleting or migrating an option can't be undone through the API, and the row values
    are only recoverable if they were written down first.
    """
    try:
        data = {
            "when": bf.datetime.now().isoformat(), "why": why,
            "db": db_key, "prop": prop,
            "options": [o["name"] for o in current_options(db_key, prop)],
            "rows": [{"id": pg["id"],
                      "value": (pg.get("properties", {}).get(prop, {}).get("select")
                                or {}).get("name")}
                     for pg in all_rows(db_key)],
        }
        os.makedirs(bf.DATA_DIR, exist_ok=True)
        old = []
        if os.path.exists(BACKUP_FILE):
            with open(BACKUP_FILE, encoding="utf-8") as f:
                old = json.load(f)
            if not isinstance(old, list):
                old = [old]
        old.append(data)
        with open(BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(old[-20:], f, indent=1)   # keep the last 20 restore points
        return True
    except Exception:  # noqa: BLE001 — a failed backup must not be silent
        return False


# ---------------------------------------------------------------- category pairs

def load_pairs():
    """Which subcategories sit under which category.

    Lives in a JSON file rather than in code so new subcategories created from the
    dashboard survive; seeded from the map baked into dashboard_render.
    """
    if os.path.exists(PAIRS_FILE):
        try:
            with open(PAIRS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    import dashboard_render as dr
    return json.loads(json.dumps(dr.PAIRS))


def save_pairs(pairs):
    os.makedirs(bf.DATA_DIR, exist_ok=True)
    with open(PAIRS_FILE, "w", encoding="utf-8") as f:
        json.dump(pairs, f, indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- notion

def notion(method, path, body=None):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {STATE['token']}",
            "Notion-Version": bf.NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read())


def load_schema():
    """Live select options per database — the edit dropdowns are built from these, so a
    stale hard-coded list can never put an invalid value into Notion."""
    out = {}
    for key, db_id in STATE["db_ids"].items():
        try:
            db = notion("GET", f"/databases/{db_id}")
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
        opts = {}
        for name, p in db.get("properties", {}).items():
            if p.get("type") == "select":
                opts[name] = [o["name"] for o in p["select"]["options"]]
        out[key] = opts
    STATE["schema"] = out
    return out


def build_props(db, changes):
    """Turn {field: value} into a Notion properties payload, validating as we go.

    Raises ValueError with a human message on anything invalid — better a clear refusal
    than a silently-created junk select option, which is invisible in Notion and quietly
    breaks every grouped report.
    """
    fmap = FIELD_MAP.get(db)
    if not fmap:
        raise ValueError(f"unknown database {db!r}")
    opts = STATE["schema"].get(db, {})
    props = {}

    for field, value in changes.items():
        if field not in fmap:
            raise ValueError(f"'{field}' can't be edited on the {db} sheet")
        prop, kind = fmap[field]
        blank = value in (None, "")

        if kind == "title":
            if blank:
                raise ValueError("the item name can't be empty")
            props[prop] = {"title": [{"text": {"content": str(value)[:2000]}}]}
        elif kind == "text":
            props[prop] = {"rich_text": ([] if blank
                                         else [{"text": {"content": str(value)[:2000]}}])}
        elif kind == "number":
            if blank:
                props[prop] = {"number": None}
            else:
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{prop} must be a number, got {value!r}")
                if prop == "Amount" and n <= 0:
                    raise ValueError("amount must be more than zero")
                props[prop] = {"number": round(n, 2)}
        elif kind == "date":
            if blank:
                raise ValueError("date can't be empty")
            v = str(value)[:10]
            try:
                bf.datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"date must look like 2026-08-07, got {value!r}")
            props[prop] = {"date": {"start": v}}
        elif kind == "select":
            if blank:
                props[prop] = {"select": None}
            else:
                allowed = opts.get(prop, [])
                if value not in allowed:
                    raise ValueError(
                        f"{value!r} isn't an existing {prop} option on the {db} sheet")
                props[prop] = {"select": {"name": value}}
        elif kind == "yesno":
            if blank:
                props[prop] = {"select": None}
            else:
                name = "Yes" if value in (True, "Yes", "yes", "true", 1) else "No"
                props[prop] = {"select": {"name": name}}
    if not props:
        raise ValueError("nothing to change")
    return props


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stdout.write("  %s %s\n" % (self.command, self.path))

    # ---- helpers
    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def send_html(self):
        path = bf.OUT_HTML
        if not os.path.exists(path):
            self.send_json({"error": "dashboard.html not built yet"}, 500)
            return
        with open(path, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    # ---- routes
    def do_GET(self):
        route = self.path.split("?")[0]
        if route in ("/", "/index.html", "/dashboard.html"):
            return self.send_html()
        if route == "/api/schema":
            return self.send_json({"ok": True, "schema": STATE["schema"],
                                   "lists": LIST_PROPS, "classes": COST_CLASSES,
                                   "pairs": load_pairs()})
        if route == "/api/lists":
            db = "business"
            if "?" in self.path:
                from urllib.parse import parse_qs
                db = (parse_qs(self.path.split("?", 1)[1]).get("db") or ["business"])[0]
            if db not in STATE["db_ids"]:
                return self.send_json({"error": f"unknown sheet {db!r}"}, 400)
            try:
                out = {}
                for prop, label in LIST_PROPS.get(db, []):
                    counts = usage_counts(db, prop)
                    out[prop] = {
                        "label": label,
                        "options": [{"name": o["name"], "used": counts.get(o["name"], 0)}
                                    for o in current_options(db, prop)],
                    }
            except (ValueError, urllib.error.HTTPError, urllib.error.URLError) as e:
                return self.send_json({"error": str(e)}, 502)
            return self.send_json({"ok": True, "db": db, "lists": out,
                                   "pairs": load_pairs()})
        if route == "/api/ping":
            return self.send_json({"ok": True, "editable": True})
        if route == "/api/rows":
            # Re-pull and re-shape just the rows, so a list change that moved entries
            # shows up in the charts without regenerating the whole file.
            try:
                token = STATE["token"]
                biz = bf.flatten(bf.notion_query(bf.BUSINESS_DB, token))
                per = bf.flatten(bf.notion_query(bf.PERSONAL_DB, token))
                expenses = bf.shape_expenses(biz)[0]
                personal = bf.shape_personal(per)[0]
                dad = []
                if "dad" in STATE["db_ids"]:
                    dad = bf.shape_shared(
                        bf.flatten(bf.notion_query(STATE["db_ids"]["dad"], token)))
            except (SystemExit, urllib.error.HTTPError, urllib.error.URLError) as e:
                return self.send_json({"error": str(e)}, 502)
            return self.send_json({"ok": True, "data": {
                "business": expenses, "personal": personal, "dad": dad}})
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        route = self.path.split("?")[0]
        try:
            body = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "bad JSON"}, 400)

        if route == "/api/update":
            return self.do_update(body)
        if route == "/api/bulk":
            return self.do_bulk(body)
        if route == "/api/archive":
            return self.do_archive(body)
        if route == "/api/rebuild":
            return self.do_rebuild()
        if route == "/api/option/add":
            return self.guard(self.do_option_add, body)
        if route == "/api/option/rename":
            return self.guard(self.do_option_rename, body)
        if route == "/api/option/delete":
            return self.guard(self.do_option_delete, body)
        if route == "/api/pairs":
            return self.guard(self.do_pairs, body)
        self.send_json({"error": "not found"}, 404)

    def guard(self, fn, body):
        """Turn a refusal into a clear 400 and a Notion failure into a clear 502."""
        try:
            return fn(body)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            return self.send_json({"error": f"Notion said {e.code}: {detail}"}, 502)
        except urllib.error.URLError as e:
            return self.send_json({"error": f"can't reach Notion ({e.reason})"}, 502)

    # ---- actions
    def do_update(self, body):
        page_id, db = body.get("id"), body.get("db")
        changes = body.get("changes") or {}
        if not page_id or not db:
            return self.send_json({"error": "missing id or db"}, 400)
        try:
            props = build_props(db, changes)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        try:
            notion("PATCH", f"/pages/{page_id}", {"properties": props})
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            return self.send_json({"error": f"Notion said {e.code}: {detail}"}, 502)
        except urllib.error.URLError as e:
            return self.send_json({"error": f"can't reach Notion ({e.reason})"}, 502)
        return self.send_json({"ok": True, "changed": list(changes)})

    def do_bulk(self, body):
        ids, db = body.get("ids") or [], body.get("db")
        changes = body.get("changes") or {}
        if not ids or not db:
            return self.send_json({"error": "missing ids or db"}, 400)
        try:
            props = build_props(db, changes)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)

        done, failed = [], []
        for pid in ids:
            try:
                notion("PATCH", f"/pages/{pid}", {"properties": props})
                done.append(pid)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                failed.append({"id": pid, "why": str(e)})
        # Partial success is reported honestly rather than rolled back — Notion has no
        # transaction, so pretending all-or-nothing would be a lie.
        return self.send_json({"ok": not failed, "done": done, "failed": failed})

    def do_archive(self, body):
        ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
        if not ids:
            return self.send_json({"error": "missing id"}, 400)
        done, failed = [], []
        for pid in ids:
            try:
                notion("PATCH", f"/pages/{pid}", {"archived": True})
                done.append(pid)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                failed.append({"id": pid, "why": str(e)})
        return self.send_json({"ok": not failed, "done": done, "failed": failed})

    # ---- list management
    def do_option_add(self, body):
        db = body.get("db")
        prop = body.get("prop")
        name = (body.get("name") or "").strip()
        if db not in STATE["db_ids"]:
            raise ValueError(f"unknown sheet {db!r}")
        if prop not in [p for p, _ in LIST_PROPS.get(db, [])]:
            raise ValueError(f"{prop!r} isn't a list on the {db} sheet")
        if not name:
            raise ValueError("give the new option a name")
        if SEP in name and not (db == "business" and prop == "Category"):
            raise ValueError("that name contains the ' · ' separator, which is reserved")

        if db == "business" and prop == "Category":
            cls = (body.get("cls") or "").strip().upper()
            if cls not in COST_CLASSES:
                raise ValueError(
                    "a business category needs a class: " + " / ".join(COST_CLASSES))
            bare = name.split(SEP)[-1].strip()
            if not bare:
                raise ValueError("give the category a name after the class")
            name = f"{cls}{SEP}{bare}"

        names = [o["name"] for o in current_options(db, prop)]
        if name in names:
            raise ValueError(f"{name!r} already exists")
        after = set_options(db, prop, names + [name])

        note = None
        if prop == "Subcategory":
            parent = body.get("parent")
            pairs = load_pairs()
            slot = pairs.setdefault(db, {})
            if parent:
                slot.setdefault(parent, [])
                if name not in slot[parent]:
                    slot[parent].append(name)
                save_pairs(pairs)
            else:
                note = ("added, but not attached to a category — it won't appear in the "
                        "edit dropdown until you attach it")
        if prop == "Category" and body.get("cls"):
            pairs = load_pairs()
            pairs.setdefault(db, {}).setdefault(name, [])
            save_pairs(pairs)
        return self.send_json({"ok": True, "name": name, "options": after, "note": note,
                               "pairs": load_pairs()})

    def do_option_rename(self, body):
        """Notion's API cannot rename a select option — it matches by id and ignores a
        changed name (verified 7 Aug 2026). So do it properly: create the new option,
        move every row across, then remove the old one. Same end result, and the row
        values are never lost because the move happens before the delete."""
        db, prop = body.get("db"), body.get("prop")
        old = (body.get("from") or "").strip()
        new = (body.get("to") or "").strip()
        if db not in STATE["db_ids"]:
            raise ValueError(f"unknown sheet {db!r}")
        if prop not in [p for p, _ in LIST_PROPS.get(db, [])]:
            raise ValueError(f"{prop!r} isn't a list on the {db} sheet")
        if not old or not new:
            raise ValueError("need both the old and the new name")
        if old == new:
            raise ValueError("that's the same name")

        names = [o["name"] for o in current_options(db, prop)]
        if old not in names:
            raise ValueError(f"{old!r} isn't in the list")
        if db == "business" and prop == "Category":
            cls = new.split(SEP)[0].strip().upper() if SEP in new else None
            if cls not in COST_CLASSES:
                raise ValueError(
                    "a business category must start with " + " / ".join(COST_CLASSES)
                    + " followed by ' · '")
        if new in names:
            raise ValueError(f"{new!r} already exists — merge into it with Delete instead")

        snapshot(db, prop, f"rename {old} -> {new}")
        set_options(db, prop, [n for n in names] + [new])

        moved, failed = 0, []
        for pid in rows_using(db, prop, old):
            try:
                notion("PATCH", f"/pages/{pid}",
                       {"properties": {prop: {"select": {"name": new}}}})
                moved += 1
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                failed.append(str(e))
        if failed:
            # Leave the old option in place — deleting it now would blank the rows that
            # didn't move.
            return self.send_json({"ok": False, "moved": moved,
                                   "error": f"{len(failed)} row(s) wouldn't move, so "
                                            f"{old!r} was kept. Try again."}, 502)

        after = set_options(db, prop, [n for n in names if n != old] + [new])

        pairs = load_pairs()
        slot = pairs.get(db, {})
        if prop == "Category" and old in slot:
            slot[new] = slot.pop(old)
        if prop == "Subcategory":
            for cat, subs in slot.items():
                slot[cat] = [new if s == old else s for s in subs]
        save_pairs(pairs)
        return self.send_json({"ok": True, "moved": moved, "options": after,
                               "pairs": load_pairs()})

    def do_option_delete(self, body):
        db, prop = body.get("db"), body.get("prop")
        name = (body.get("name") or "").strip()
        move_to = body.get("reassign_to")
        if db not in STATE["db_ids"]:
            raise ValueError(f"unknown sheet {db!r}")
        if prop not in [p for p, _ in LIST_PROPS.get(db, [])]:
            raise ValueError(f"{prop!r} isn't a list on the {db} sheet")
        names = [o["name"] for o in current_options(db, prop)]
        if name not in names:
            raise ValueError(f"{name!r} isn't in the list")

        using = rows_using(db, prop, name)
        if using and not move_to:
            # Refusing beats silently blanking 45 rows.
            raise ValueError(
                f"{len(using)} row(s) still use {name!r}. Pick something to move them to "
                f"first, or change those rows.")
        snapshot(db, prop, f"delete {name}")

        moved = 0
        if using:
            if move_to not in names or move_to == name:
                raise ValueError(f"{move_to!r} isn't a valid option to move them to")
            for pid in using:
                notion("PATCH", f"/pages/{pid}",
                       {"properties": {prop: {"select": {"name": move_to}}}})
                moved += 1

        after = set_options(db, prop, [n for n in names if n != name])
        pairs = load_pairs()
        slot = pairs.get(db, {})
        if prop == "Category":
            slot.pop(name, None)
        if prop == "Subcategory":
            for cat, subs in slot.items():
                slot[cat] = [s for s in subs if s != name]
        save_pairs(pairs)
        return self.send_json({"ok": True, "moved": moved, "options": after,
                               "pairs": load_pairs()})

    def do_pairs(self, body):
        """Re-attach a subcategory to a different category (dashboard-side grouping only —
        Notion keeps Subcategory as one flat list)."""
        db = body.get("db")
        sub = (body.get("sub") or "").strip()
        parent = body.get("parent")
        if db not in STATE["db_ids"] or not sub:
            raise ValueError("need a sheet and a subcategory")
        pairs = load_pairs()
        slot = pairs.setdefault(db, {})
        for cat in list(slot):
            slot[cat] = [s for s in slot[cat] if s != sub]
        if parent:
            slot.setdefault(parent, []).append(sub)
        save_pairs(pairs)
        return self.send_json({"ok": True, "pairs": load_pairs()})

    def do_rebuild(self):
        try:
            bf.main()
            load_schema()
        except SystemExit as e:
            return self.send_json({"error": str(e)}, 500)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self.send_json({"ok": True})


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Serve the finance dashboard, editable.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--local-only", action="store_true",
                    help="bind to localhost only — nothing on the wifi can reach it")
    ap.add_argument("--no-build", action="store_true",
                    help="serve the existing dashboard.html without refreshing first")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    token = bf.get_token()
    if not token:
        raise SystemExit(
            "\n  No .notion_token found — run Connect-Notion.bat first.\n")
    STATE["token"] = token

    if not args.no_build:
        print("\n  Refreshing from Notion first ...")
        bf.main()

    dad_id, _ = bf.find_dad_db(token)
    STATE["db_ids"] = {"business": bf.BUSINESS_DB, "personal": bf.PERSONAL_DB}
    if dad_id:
        STATE["db_ids"]["dad"] = dad_id
    load_schema()

    host = "127.0.0.1" if args.local_only else "0.0.0.0"
    httpd = ThreadingHTTPServer((host, args.port), Handler)
    httpd.daemon_threads = True

    url = f"http://localhost:{args.port}/"
    print("\n  " + "-" * 52)
    print("   OG GAINZ MONEY — editable dashboard is running")
    print("  " + "-" * 52)
    print(f"   On this laptop : {url}")
    if not args.local_only:
        ip = lan_ip()
        if ip:
            print(f"   On your phone  : http://{ip}:{args.port}/")
            print("                    (same wifi, laptop must stay awake)")
        print("\n   No password — anyone on your wifi can open this while it runs.")
        print("   Use  --local-only  on shared wifi.")
    print("\n   Edits save straight to Notion. Close this window to stop.")
    print("  " + "-" * 52 + "\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")


if __name__ == "__main__":
    main()
