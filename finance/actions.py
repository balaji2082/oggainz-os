"""
Everything the finance dashboard can DO, as plain functions.

This exists so the two front doors — `serve_dashboard.py` on the laptop and the
Money tab in the Command Center — run the same code instead of two copies that
drift. A category rule fixed in one place is fixed in both.

Every function here returns `(payload_dict, http_status)` and never touches a
socket, a Flask request or a `self`. Refusals come back as a 400 with a sentence
a human can act on; a Notion failure comes back as a 502 saying so. Nothing here
raises for an ordinary bad input — the caller only has to serialise the result.

The Notion state (token, database ids, live select options) lives in `STATE`
here, which `connect()` fills in. Both front doors call `connect()` at start-up.
This module must not import either front door — they import it.
"""

import json
import os
import urllib.error

import build_finance as bf


API = "https://api.notion.com/v1"

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


# ---------------------------------------------------------------- connection

def connect(token=None):
    """Point the module at Notion and load the live schema.

    Returns (ok, message). Safe to call repeatedly — the Command Center calls it
    on the first Money request and again whenever the token changes.
    """
    token = token or bf.get_token()
    if not token:
        return False, ("No Notion token. Put it in .notion_token on the laptop, or set "
                       "OG_NOTION_TOKEN on the server.")
    STATE["token"] = token
    ids = {"business": bf.BUSINESS_DB, "personal": bf.PERSONAL_DB}
    dad_id, _info = bf.find_dad_db(token)
    if dad_id:
        ids["dad"] = dad_id
    STATE["db_ids"] = ids
    try:
        load_schema()
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        return False, f"Can't reach Notion ({e})"
    return True, "connected"


def connected():
    return bool(STATE.get("token") and STATE.get("db_ids"))


def _guard(fn, *a, **kw):
    """Run an action, turning the two expected failures into honest statuses."""
    try:
        return fn(*a, **kw)
    except ValueError as e:
        return {"error": str(e)}, 400
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return {"error": f"Notion said {e.code}: {detail}"}, 502
    except urllib.error.URLError as e:
        return {"error": f"can't reach Notion ({e.reason})"}, 502


# ---------------------------------------------------------------- reads

def schema():
    return {"ok": True, "schema": STATE["schema"], "lists": LIST_PROPS,
            "classes": COST_CLASSES, "pairs": load_pairs()}, 200


def lists(db="business"):
    if db not in STATE["db_ids"]:
        return {"error": f"unknown sheet {db!r}"}, 400

    def _run():
        out = {}
        for prop, label in LIST_PROPS.get(db, []):
            counts = usage_counts(db, prop)
            out[prop] = {"label": label,
                         "options": [{"name": o["name"], "used": counts.get(o["name"], 0)}
                                     for o in current_options(db, prop)]}
        return {"ok": True, "db": db, "lists": out, "pairs": load_pairs()}, 200

    return _guard(_run)


def rows():
    """Re-pull and re-shape just the rows, so a list change that moved entries
    shows up in the charts without regenerating the whole dashboard."""
    def _run():
        token = STATE["token"]
        biz = bf.flatten(bf.notion_query(bf.BUSINESS_DB, token))
        per = bf.flatten(bf.notion_query(bf.PERSONAL_DB, token))
        out = {"business": bf.shape_expenses(biz)[0],
               "personal": bf.shape_personal(per)[0], "dad": []}
        if "dad" in STATE["db_ids"]:
            out["dad"] = bf.shape_shared(
                bf.flatten(bf.notion_query(STATE["db_ids"]["dad"], token)))
        return {"ok": True, "data": out}, 200

    try:
        return _guard(_run)
    except SystemExit as e:            # notion_query exits on a bad token
        return {"error": str(e)}, 502


# ---------------------------------------------------------------- writes

def update(body):
    page_id, db = body.get("id"), body.get("db")
    changes = body.get("changes") or {}
    if not page_id or not db:
        return {"error": "missing id or db"}, 400

    def _run():
        props = build_props(db, changes)
        notion("PATCH", f"/pages/{page_id}", {"properties": props})
        return {"ok": True, "changed": list(changes)}, 200

    return _guard(_run)


def bulk(body):
    ids, db = body.get("ids") or [], body.get("db")
    changes = body.get("changes") or {}
    if not ids or not db:
        return {"error": "missing ids or db"}, 400
    try:
        props = build_props(db, changes)
    except ValueError as e:
        return {"error": str(e)}, 400

    done, failed = [], []
    for pid in ids:
        try:
            notion("PATCH", f"/pages/{pid}", {"properties": props})
            done.append(pid)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            failed.append({"id": pid, "why": str(e)})
    # Partial success is reported honestly rather than rolled back — Notion has no
    # transaction, so pretending all-or-nothing would be a lie.
    return {"ok": not failed, "done": done, "failed": failed}, 200


def archive(body):
    ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
    if not ids:
        return {"error": "missing id"}, 400
    done, failed = [], []
    for pid in ids:
        try:
            notion("PATCH", f"/pages/{pid}", {"archived": True})
            done.append(pid)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            failed.append({"id": pid, "why": str(e)})
    return {"ok": not failed, "done": done, "failed": failed}, 200


# ---------------------------------------------------------------- list management

def option_add(body):
    def _run():
        db, prop = body.get("db"), body.get("prop")
        name = (body.get("name") or "").strip()
        _check_list(db, prop)
        if not name:
            raise ValueError("give the new option a name")
        if SEP in name and not (db == "business" and prop == "Category"):
            raise ValueError("that name contains the ' · ' separator, which is reserved")

        if db == "business" and prop == "Category":
            cls = (body.get("cls") or "").strip().upper()
            if cls not in COST_CLASSES:
                raise ValueError("a business category needs a class: "
                                 + " / ".join(COST_CLASSES))
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
        return {"ok": True, "name": name, "options": after, "note": note,
                "pairs": load_pairs()}, 200

    return _guard(_run)


def option_rename(body):
    """Notion's API cannot rename a select option — it matches by id and ignores a
    changed name (verified 7 Aug 2026). So do it properly: create the new option,
    move every row across, then remove the old one. Same end result, and the row
    values are never lost because the move happens before the delete."""
    def _run():
        db, prop = body.get("db"), body.get("prop")
        old = (body.get("from") or "").strip()
        new = (body.get("to") or "").strip()
        _check_list(db, prop)
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
                raise ValueError("a business category must start with "
                                 + " / ".join(COST_CLASSES) + " followed by ' · '")
        if new in names:
            raise ValueError(f"{new!r} already exists — merge into it with Delete instead")

        snapshot(db, prop, f"rename {old} -> {new}")
        set_options(db, prop, list(names) + [new])

        moved, failed = 0, []
        for pid in rows_using(db, prop, old):
            try:
                notion("PATCH", f"/pages/{pid}",
                          {"properties": {prop: {"select": {"name": new}}}})
                moved += 1
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                failed.append(str(e))
        if failed:
            # Leave the old option in place — deleting it now would blank the rows
            # that didn't move.
            return {"ok": False, "moved": moved,
                    "error": f"{len(failed)} row(s) wouldn't move, so {old!r} was kept. "
                             f"Try again."}, 502

        after = set_options(db, prop, [n for n in names if n != old] + [new])
        pairs = load_pairs()
        slot = pairs.get(db, {})
        if prop == "Category" and old in slot:
            slot[new] = slot.pop(old)
        if prop == "Subcategory":
            for cat, subs in slot.items():
                slot[cat] = [new if s == old else s for s in subs]
        save_pairs(pairs)
        return {"ok": True, "moved": moved, "options": after, "pairs": load_pairs()}, 200

    return _guard(_run)


def option_delete(body):
    def _run():
        db, prop = body.get("db"), body.get("prop")
        name = (body.get("name") or "").strip()
        move_to = body.get("reassign_to")
        _check_list(db, prop)
        names = [o["name"] for o in current_options(db, prop)]
        if name not in names:
            raise ValueError(f"{name!r} isn't in the list")

        using = rows_using(db, prop, name)
        if using and not move_to:
            # Refusing beats silently blanking 45 rows.
            raise ValueError(f"{len(using)} row(s) still use {name!r}. Pick something to "
                             f"move them to first, or change those rows.")
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
        return {"ok": True, "moved": moved, "options": after, "pairs": load_pairs()}, 200

    return _guard(_run)


def pairs(body):
    """Re-attach a subcategory to a different category (dashboard-side grouping only —
    Notion keeps Subcategory as one flat list)."""
    def _run():
        db = body.get("db")
        sub = (body.get("sub") or "").strip()
        parent = body.get("parent")
        if db not in STATE["db_ids"] or not sub:
            raise ValueError("need a sheet and a subcategory")
        p = load_pairs()
        slot = p.setdefault(db, {})
        for cat in list(slot):
            slot[cat] = [s for s in slot[cat] if s != sub]
        if parent:
            slot.setdefault(parent, []).append(sub)
        save_pairs(p)
        return {"ok": True, "pairs": load_pairs()}, 200

    return _guard(_run)


def rebuild():
    """Re-pull everything from Notion and regenerate the dashboard file."""
    try:
        bf.main()
        load_schema()
    except SystemExit as e:
        return {"error": str(e)}, 500
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}, 500
    return {"ok": True}, 200


def _check_list(db, prop):
    if db not in STATE["db_ids"]:
        raise ValueError(f"unknown sheet {db!r}")
    if prop not in [p for p, _ in LIST_PROPS.get(db, [])]:
        raise ValueError(f"{prop!r} isn't a list on the {db} sheet")


# The POST routes both front doors expose, so neither can forget one.
POST_ACTIONS = {
    "update": update,
    "bulk": bulk,
    "archive": archive,
    "option/add": option_add,
    "option/rename": option_rename,
    "option/delete": option_delete,
    "pairs": pairs,
}
