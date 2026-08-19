"""
OG Gainz Command Center — Flask web app.

Run:  python app.py        (then open the URL it prints; works on PC + phone)
"""

import os
import time
import socket
from datetime import date, datetime, timedelta

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, send_file, flash, abort, session)

import json
import secrets

import db
import core
import crm
import menu
import ops
import sheet_i18n
import agent
import brochure

# Run all date logic in the user's timezone (IST) even when the app is hosted on a
# server whose clock is UTC (e.g. PythonAnywhere). Without this, "today" on the server
# can be a different calendar day than in India, so day-based counts and lists go wrong.
# tzset() is POSIX-only; on Windows (local dev) there is no tzset and the machine clock
# is already IST, so we leave it untouched there.
if hasattr(time, "tzset"):
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()

app = Flask(__name__)
# Secret key signs the login cookie. On the public server it is supplied via the
# OG_SECRET_KEY environment variable; the fallback is only ever used for local runs.
app.secret_key = os.environ.get("OG_SECRET_KEY", "og-gainz-local-dev-only")

# Jinja prints a None as the literal text "None". In an edit form that means an
# empty column comes back as the four characters N-o-n-e, and saving writes that
# string into the database — so a blank note becomes "None", and a blank address
# would print "ADDRESS: None" on the rider's sheet. Render None as empty instead.
app.jinja_env.finalize = lambda v: "" if v is None else v
# Pick up template edits on the next page refresh (no app restart needed for HTML changes).
# Note: changes to the Python files (app.py / core.py) still require restarting the app.
app.config["TEMPLATES_AUTO_RELOAD"] = True
# Stay logged in for ~30 days so the phone isn't asking for the password every visit.
app.permanent_session_lifetime = timedelta(days=30)

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Make sure the database + tables exist as soon as this module is imported.
# The public web server imports `app` directly (it never runs the __main__ block
# below), so initialisation has to happen here too. init_db() is safe to repeat.
db.init_db()

# Seed the menu engine (component catalog + brochure pack recipes/prices) once,
# then the editable digital brochure (built from those live plans/prices) once.
_c = db.get_db()
menu.seed_menu(_c)
brochure.seed_brochure(_c)
_c.close()

# ---------------------------------------------------------------------------
# Login gate
# ---------------------------------------------------------------------------
# Protection switches ON only when OG_PASSWORD is set (we set it on the public
# server). With no password configured, the app behaves exactly as before for
# local use on your laptop — no login screen.
APP_PASSWORD = os.environ.get("OG_PASSWORD")
# Editor friends log in with this second password: they get role "team" and can
# reach ONLY the /content pages (content_hub gates the rest). Optional.
TEAM_PASSWORD = os.environ.get("OG_TEAM_PASSWORD")
# invoice_public + terms + brochure are customer-facing pages (token-protected /
# plain info), so they stay reachable without the app password.
# content_sync_api is protected by its own token (OG_SYNC_TOKEN), not the session.
PUBLIC_ENDPOINTS = {"login", "static", "invoice_public", "terms", "brochure_public",
                    "content_sync_api"}

# The customer API (customer_api.py) is machine-to-machine — the new OG Gainz
# app's server calls it with a service token. It is exempt from the HUMAN login
# gate below and guarded by its own token check instead. Matched on the path
# prefix rather than a list of endpoint names, so endpoints added to the API
# later cannot be forgotten here and accidentally answer with a login page.
API_PREFIX = "/api/v1/"


@app.before_request
def require_login():
    if request.path.startswith(API_PREFIX):
        return                                   # token-guarded in customer_api.py
    if not APP_PASSWORD:
        return                                   # local mode: no gate
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if session.get("authed"):
        return
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD or session.get("authed"):
        return redirect(url_for("today"))
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session.permanent = True
            session["authed"] = True
            session["role"] = "admin"
            # Only follow same-site relative paths (never an external URL).
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("today")
            return redirect(nxt)
        if TEAM_PASSWORD and request.form.get("password") == TEAM_PASSWORD:
            session.permanent = True
            session["authed"] = True
            session["role"] = "team"          # content pages only (content_hub gate)
            return redirect(url_for("content_board"))
        error = "Wrong password — try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# Content Hub (social-media production dashboard) — self-contained module.
import content_hub                                            # noqa: E402
content_hub.register(app)

# Money — the finance dashboard, reachable from the phone. Admin-only, and off
# entirely unless OG_NOTION_TOKEN is set on the server (see finance_hub.py for
# why the gate is where it is). Adds routes only; changes no existing view.
import finance_hub                                            # noqa: E402
finance_hub.register(app)

# Read-only customer API for the new OG Gainz app. Additive: it adds routes and
# changes no existing view. Off unless OG_API_TOKEN is set.
import customer_api                                           # noqa: E402
customer_api.register(app)

# "Invite to app" button on a customer's page. Off unless OG_APP_URL and
# OG_ADMIN_TOKEN are set, and it never writes to this database.
import app_invites                                            # noqa: E402
app_invites.register(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def conn():
    return db.get_db()


def plain(v):
    """3.0 -> '3', 3.5 -> '3.5'. A per-km rate is read off a printed sheet by the
    person being paid on it, so it should look like the number he was told."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return v
    return str(int(n)) if n == int(n) else f"{n:g}"


def inr(v):
    """Format a number as Indian rupees with lakh/crore grouping."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "₹0"
    neg = n < 0
    n = abs(int(round(n)))
    s = str(n)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts) + "," + last3
    return ("-₹" if neg else "₹") + s


def fmt_date(v, fmt="%d %b"):
    if not v:
        return ""
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime(fmt)
    except ValueError:
        return str(v)


def wa_link(phone):
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        digits = "91" + digits
    return "https://wa.me/" + digits


app.jinja_env.filters["inr"] = inr
app.jinja_env.filters["plain"] = plain
app.jinja_env.filters["fdate"] = fmt_date
app.jinja_env.filters["wa"] = wa_link


def next_id(c, table, prefix, width=3):
    rows = c.execute(f"SELECT id FROM {table} WHERE id LIKE ?", (prefix + "%",)).fetchall()
    mx = 0
    for r in rows:
        tail = "".join(ch for ch in r["id"][len(prefix):] if ch.isdigit())
        if tail:
            mx = max(mx, int(tail))
    return f"{prefix}{mx + 1:0{width}d}"


def _wants_json():
    """True when the caller is our fetch() helper (so we answer JSON, not a redirect)."""
    return request.headers.get("X-Requested-With") == "fetch"


def today_iso():
    # Allow ?d=YYYY-MM-DD override for browsing other days
    d = request.args.get("d")
    if d:
        try:
            datetime.strptime(d, "%Y-%m-%d")
            return d
        except ValueError:
            pass
    return date.today().strftime("%Y-%m-%d")


@app.before_request
def agent_heartbeat():
    """Once per day (first request), OG Agent builds the morning brief + alerts."""
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return
    if APP_PASSWORD and not session.get("authed"):
        return
    try:
        c = conn()
        agent.run_daily(c)
        c.close()
    except Exception:
        pass                                     # the app must never die on agent trouble


@app.context_processor
def inject_globals():
    c = conn()
    stats = core.headline_stats(c)
    due = crm.followups_due_count(c)            # CRM follow-ups due → nav badge
    unread = agent.unread_count(c)              # OG Agent bell badge
    agent_ready = agent.ai_ready(c)             # floating chat: online or key missing
    # Customers with a live subscription but no map pin — they break the route.
    unpinned = len(ops.pin_status(c)["blocking"])
    # Subscriptions whose composition the kitchen cannot see — these make the
    # prep list wrong, so they are worth a standing badge rather than a one-off.
    sub_issues = sum(1 for r in menu.audit_all(c) if r["high"])
    # Until the real kitchen pin is set, EVERY distance, zone and allowance is
    # measured from a placeholder ~2.7 km away — silently wrong on every screen.
    # This warns everywhere, not just on the two pages that happen to check.
    kitchen_unset = not ops.kitchen(c)[2]
    # Live on-demand packs: these get NO orders unless their week is ticked, so the
    # count is a standing reminder that someone is waiting to be planned.
    ondemand = c.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE COALESCE(schedule_mode,'daily')='ondemand' "
        "AND status IN ('Active','Ending Soon')").fetchone()[0]
    c.close()
    return {"g_stats": stats, "g_due": due, "g_unread": unread,
            "g_agent_ready": agent_ready, "g_unpinned": unpinned,
            "g_sub_issues": sub_issues, "g_kitchen_unset": kitchen_unset,
            "g_ondemand": ondemand,
            "active_page": request.endpoint,
            "today": date.today().strftime("%Y-%m-%d"),
            "today_str": date.today().strftime("%Y-%m-%d"),
            "login_active": bool(APP_PASSWORD), "authed": session.get("authed"),
            "asset_v": _asset_v,
            "deployed": bool(APP_PASSWORD)}


_ASSET_V = {}


def _asset_v(rel):
    """A cache-busting stamp for a static file, from its own mtime.

    The stylesheet and app.js were served on a bare URL, so a phone that had
    already opened the Command Center kept its cached copy after a deploy — a
    new button could ship and simply not appear, with no way to tell from the
    phone whether the deploy had worked. Looked up once per file per process;
    a Reload is what publishes new code anyway, and that resets it.
    """
    if rel not in _ASSET_V:
        try:
            _ASSET_V[rel] = str(int(os.path.getmtime(
                os.path.join(app.static_folder, rel))))
        except OSError:
            _ASSET_V[rel] = "0"
    return _ASSET_V[rel]


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return redirect(url_for("today"))


@app.route("/more")
def more():
    return render_template("more.html")


@app.route("/today")
def today():
    c = conn()
    core.refresh_pause_statuses(c)        # flip any freeze that has just started/ended
    d = today_iso()
    view = request.args.get("view", "all")
    summary = core.today_summary(c, d)
    ending = c.execute(
        "SELECT * FROM subscriptions WHERE status='Ending Soon' ORDER BY remaining_meals"
    ).fetchall()
    unpaid = c.execute(
        "SELECT * FROM subscriptions WHERE payment_status!='Paid' ORDER BY id"
    ).fetchall()
    override = core.day_override(c, d)
    prep = menu.prep_for_day(c, d, rows=summary["rows"])   # detailed kitchen quantities
    backlog = menu.backlog(c, date.today().strftime("%Y-%m-%d"))
    ticks = {r["key"] for r in c.execute("SELECT key FROM prep_ticks WHERE date=?", (d,))}
    prev_d = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    next_d = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    # Address lists, so a customer with more than one can be switched for today.
    addr_map = {}
    for r in summary["rows"]:
        cid = r["customer_id"]
        if cid and cid not in addr_map:
            addr_map[cid] = ops.customer_addresses(c, cid)
    variant_map = _variant_map(c, summary["rows"])
    c.close()
    return render_template("today.html", d=d, s=summary, ending=ending, unpaid=unpaid,
                           prev_d=prev_d, next_d=next_d, view=view, override=override,
                           prep=prep, backlog=backlog, ticks=ticks, addr_map=addr_map,
                           variant_map=variant_map,
                           day_label=fmt_date(d, "%A, %d %b %Y"))


@app.route("/day/toggle", methods=["POST"])
def day_toggle():
    """Force a specific day's kitchen open/closed, or clear back to the weekday default."""
    c = conn()
    d = request.form.get("d") or date.today().strftime("%Y-%m-%d")
    action = request.form.get("action")          # open | close | clear
    if action == "open":
        core.set_day_override(c, d, True, request.form.get("reason", "Working day"))
        flash(f"Kitchen opened for {fmt_date(d, '%d %b')}.", "ok")
    elif action == "close":
        core.set_day_override(c, d, False, request.form.get("reason", "Kitchen off"))
        core.purge_unworked_day(c, d)     # drop the pending orders that opening created
        flash(f"Kitchen closed for {fmt_date(d, '%d %b')}.", "ok")
    else:
        core.clear_day_override(c, d)
        if not core.is_operating_day(c, d):   # reset landed on a non-working day
            core.purge_unworked_day(c, d)
        flash(f"Reset {fmt_date(d, '%d %b')} to the normal schedule.", "ok")
    c.close()
    return redirect(request.referrer or url_for("today", d=d))


@app.route("/api/delivery/<int:did>/mark", methods=["POST"])
def api_mark(did):
    status = request.form.get("status", "Delivered")
    c = conn()

    # [LAW] Balaji, 13 Aug 2026 — a meal the CUSTOMER skipped cannot be
    # un-skipped from this side. Only they can put it back, in the app.
    #
    # Without this, one mis-tap silently turns "the customer told us not to
    # cook" into "cook and deliver this". They are not expecting food and
    # nobody tells them it is coming, so a meal is cooked and wasted — or
    # delivered to a house nobody is in. The failure is quiet, which is what
    # makes it dangerous: nothing on the screen would look wrong afterwards.
    #
    # Balaji's own skips are untouched. `skip_source` is NULL on every row he
    # marked himself, including every one that existed before the column did.
    row = c.execute("SELECT status, skip_source FROM deliveries WHERE id=?", (did,)).fetchone()
    if row is not None and row["status"] == "Skipped" \
            and ("skip_source" in row.keys() and row["skip_source"] == "customer"):
        c.close()
        return jsonify({
            "ok": False, "locked": True,
            "error": "The customer skipped this meal in the app. "
                     "Only they can put it back — ask them to undo it there.",
        }), 409

    core.mark_delivery(c, did, status)
    d = today_iso()
    s = core.today_summary(c, d)
    c.close()
    return jsonify({"ok": True, "status": status,
                    "done": s["done"], "pending": s["pending"],
                    "skipped": s["skipped"], "total": s["total"]})


@app.route("/api/prep/tick", methods=["POST"])
def api_prep_tick():
    """Toggle one prep-list item's 'prepped ✓' tick for a day. A pure kitchen
    self-check — it never touches orders, meals or subscriptions."""
    d = request.form.get("d") or date.today().strftime("%Y-%m-%d")
    key = (request.form.get("key") or "").strip()
    on = request.form.get("on") == "1"
    if not key:
        return jsonify({"ok": False})
    c = conn()
    if on:
        c.execute("INSERT OR IGNORE INTO prep_ticks(date,key) VALUES(?,?)", (d, key))
    else:
        c.execute("DELETE FROM prep_ticks WHERE date=? AND key=?", (d, key))
    c.commit(); c.close()
    return jsonify({"ok": True, "on": on})


@app.route("/api/delivery/<int:did>/note", methods=["POST"])
def api_note(did):
    """Save/clear the one-off note on a single day's order (slot swap, same-price plan
    change, today-only customisation, a food issue to remember). Kept on the row so it
    shows up in that date's list and in the customer's delivery history forever."""
    c = conn()
    note = core.set_delivery_note(c, did, request.form.get("note", ""))
    c.close()
    return jsonify({"ok": True, "note": note or ""})


# ---------------------------------------------------------------------------
# Deliveries / meal tracker history
# ---------------------------------------------------------------------------
@app.route("/deliveries")
def deliveries():
    c = conn()
    d = today_iso()
    view = request.args.get("view", "all")
    rows = core.deliveries_for_day(c, d)
    override = core.day_override(c, d)
    operating = core.is_operating_day(c, d)
    prev_d = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    next_d = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    variant_map = _variant_map(c, rows)
    c.close()
    return render_template("deliveries.html", d=d, rows=rows, view=view, override=override,
                           operating=operating, prev_d=prev_d, next_d=next_d,
                           variant_map=variant_map,
                           day_label=fmt_date(d, "%A, %d %b %Y"))


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
def _menu_data(c):
    """Catalog + recipes + prices as one JSON blob the subscription form's JS uses
    for pack preview, add-on pricing, and the custom builder."""
    comps = [{"id": r["id"], "name": r["name"], "unit": r["unit"], "step": r["step"],
              "default_qty": r["default_qty"], "addon_price": r["addon_price"],
              "protein": r["protein"], "emoji": r["emoji"]}
             for r in menu.all_components(c)]
    plans_out = {}
    for p in c.execute("SELECT * FROM plans WHERE active=1").fetchall():
        plans_out[p["name"]] = {
            "id": p["id"], "monthly": p["price_monthly"], "weekly": p["price_weekly"],
            "trial": p["price_trial"], "validity": p["validity_days"],
            "recipe": [{"id": r["component_id"], "qty": r["qty"], "note": r["note"] or ""}
                       for r in menu.plan_recipe(c, p["id"])],
        }
    return {"components": comps, "plans": plans_out}


def _save_schedule_from_form(c, sid, f, vmap=None):
    """Persist the delivery schedule the create/edit form submitted.

    The form posts `schedule_mode` plus, for 'weekly', one `day_active_<n>` per
    ticked weekday with optional `day_slot_<n>` / `day_variant_<n>`. A form that
    sends no schedule_mode at all (an old bookmark, or the CRM conversion flow)
    leaves the subscription exactly as it was rather than silently resetting it
    to daily.

    `vmap` translates old variant ids to new ones when renewing: the prefilled
    form is rendered from the pack being copied, so the ids it posts belong to
    THAT subscription. Anything that still doesn't belong here after remapping is
    dropped rather than stored — a day plan pointing at another subscription's
    box would cook the wrong meal and be near-impossible to spot.
    """
    mode = f.get("schedule_mode")
    if mode not in ("daily", "weekly", "ondemand"):
        return
    c.execute("UPDATE subscriptions SET schedule_mode=? WHERE id=?", (mode, sid))
    c.commit()
    if mode == "weekly":
        mine = {v["id"] for v in menu.variants_for_sub(c, sid)}
        rows = []
        for wd in range(7):
            if not f.get(f"day_active_{wd}"):
                continue
            vid = f.get(f"day_variant_{wd}") or None
            if vid:
                vid = int(vid)
                vid = (vmap or {}).get(vid, vid)
                if vid not in mine:
                    vid = None
            rows.append({"weekday": wd, "active": 1,
                         "slot": f.get(f"day_slot_{wd}") or None,
                         "variant_id": vid,
                         "meals": f.get(f"day_meals_{wd}") or 1,
                         "note": f.get(f"day_note_{wd}") or None})
        core.save_day_plan(c, sid, rows)
    else:
        # daily and ondemand don't read the weekday grid; clear it so a later
        # switch back to weekly starts from a clean, visible state.
        c.execute("DELETE FROM subscription_day_plan WHERE subscription_id=?", (sid,))
        c.commit()
    # Bring already-planned future orders in line with the new schedule.
    core.resync_scheduled_days(c, sid)


def _copy_pack_setup(c, from_sid, to_sid, skip_composition=False):
    """Copy everything that makes a pack what it is onto a renewal.

    A renewal used to copy only the subscription row, so the new pack arrived with
    no contents at all and every add-on had to be typed in again by hand. What a
    customer is actually getting is spread across four tables, and all four have to
    travel: the box contents, any alternative boxes, the weekly schedule that says
    which box lands on which day, and the standing rules.

    Variant ids are remapped as they are copied, so the new pack's day plan points
    at the new pack's variants and the two subscriptions stay independent — editing
    the renewal can never reach back and change last month's record. Returns that
    {old_variant_id: new_variant_id} map, which the caller needs to translate the
    ids a prefilled renewal form posts back.
    """
    # Variants first: we need old id -> new id before anything referencing them.
    vmap = {}
    for v in c.execute("SELECT * FROM subscription_variants WHERE subscription_id=? ORDER BY id",
                       (from_sid,)).fetchall():
        cur = c.execute(
            "INSERT INTO subscription_variants(subscription_id,name,plan_id,sort,created_at) "
            "VALUES(?,?,?,?,?)", (to_sid, v["name"], v["plan_id"], v["sort"], db.now_iso()))
        vmap[v["id"]] = cur.lastrowid

    if not skip_composition:
        c.execute("DELETE FROM subscription_components WHERE subscription_id=?", (to_sid,))
    for r in c.execute("SELECT * FROM subscription_components WHERE subscription_id=?",
                       (from_sid,)).fetchall():
        # When the form supplied its own contents, keep those for the normal box but
        # still bring the variants across — the form has no way to describe them.
        if skip_composition and r["variant_id"] is None:
            continue
        c.execute(
            "INSERT INTO subscription_components(subscription_id,component_id,qty,kind,note,"
            "agreed_price,price_note,variant_id) VALUES(?,?,?,?,?,?,?,?)",
            (to_sid, r["component_id"], r["qty"], r["kind"], r["note"],
             r["agreed_price"], r["price_note"], vmap.get(r["variant_id"])))

    old = c.execute("SELECT schedule_mode FROM subscriptions WHERE id=?", (from_sid,)).fetchone()
    if old and old["schedule_mode"]:
        c.execute("UPDATE subscriptions SET schedule_mode=? WHERE id=?",
                  (old["schedule_mode"], to_sid))
    c.execute("DELETE FROM subscription_day_plan WHERE subscription_id=?", (to_sid,))
    for d in c.execute("SELECT * FROM subscription_day_plan WHERE subscription_id=?",
                       (from_sid,)).fetchall():
        c.execute(
            "INSERT INTO subscription_day_plan(subscription_id,weekday,active,slot,variant_id,meals,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (to_sid, d["weekday"], d["active"], d["slot"], vmap.get(d["variant_id"]),
             d["meals"], d["note"]))

    for r in c.execute("SELECT * FROM component_rules WHERE subscription_id=? AND active=1",
                       (from_sid,)).fetchall():
        c.execute(
            "INSERT INTO component_rules(subscription_id,weekdays,start_date,end_date,action,"
            "from_component_id,to_component_id,qty,note,source,active,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,1,?)",
            (to_sid, r["weekdays"], r["start_date"], r["end_date"], r["action"],
             r["from_component_id"], r["to_component_id"], r["qty"], r["note"],
             r["source"], db.now_iso()))
    c.commit()
    return vmap


def _variant_map(c, rows):
    """{subscription_id: [{id, name}]} for the subscriptions in a day's list.

    The extra-meal sheet needs it so a customer whose Tuesday is a salad can be
    given an extra salad rather than silently getting their usual box. Only the
    subscriptions actually on screen are looked up, and only once each.
    """
    out = {}
    for r in rows:
        sid = r["subscription_id"]
        if not sid or sid in out:
            continue
        vs = menu.variants_for_sub(c, sid)
        if vs:
            out[sid] = [{"id": v["id"], "name": v["name"]} for v in vs]
    return out


def _schedule_ctx(c, sid):
    """Day plan + variants for the create/edit form and the detail card."""
    return {"day_plan": core.day_plan_for(c, sid) if sid else {},
            "variants": menu.variants_for_sub(c, sid) if sid else [],
            "weekday_names": core.WEEKDAY_NAMES}


def _save_composition_from_form(c, sid, f):
    """Persist the composition JSON the form's JS assembles (base + add-ons).
    If the form didn't send one (e.g. old bookmark / JS off), fall back to copying
    the matched pack recipe so the prep list still works."""
    raw = f.get("comp_json")
    if raw:
        try:
            items = json.loads(raw)
        except ValueError:
            items = None
        if isinstance(items, list):
            base = [(int(i["id"]), float(i["qty"]), (i.get("note") or "").strip())
                    for i in items if i.get("kind") != "addon" and float(i.get("qty") or 0) > 0]
            addons = [(int(i["id"]), float(i["qty"]), (i.get("note") or "").strip())
                      for i in items if i.get("kind") == "addon" and float(i.get("qty") or 0) > 0]
            menu.save_sub_composition(c, sid, base, addons)
            return
    if not c.execute("SELECT 1 FROM subscription_components WHERE subscription_id=?",
                     (sid,)).fetchone():
        p = menu.match_plan(c, f.get("plan_name"))
        if p:
            menu.copy_recipe_to_subscription(c, p["id"], sid)
            c.commit()


@app.route("/subscriptions")
def subscriptions():
    c = conn()
    core.refresh_pause_statuses(c)        # flip any freeze that has just started/ended
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM subscriptions WHERE 1=1"
    args = []
    if status == "Active":
        # "Active" tab = every running subscription, including the Ending Soon ones.
        sql += " AND status IN ('Active','Ending Soon')"
    elif status == "Unpaid":
        sql += " AND payment_status!='Paid'"
    elif status:
        sql += " AND status=?"
        args.append(status)
    if q:
        sql += " AND (customer_name LIKE ? OR plan_name LIKE ? OR id LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY CASE status WHEN 'Ending Soon' THEN 0 WHEN 'Active' THEN 1 " \
           "WHEN 'Paused' THEN 2 ELSE 3 END, id DESC"
    rows = c.execute(sql, args).fetchall()
    counts = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM subscriptions GROUP BY status")}
    counts_unpaid = c.execute(
        "SELECT COUNT(*) n FROM subscriptions WHERE payment_status!='Paid'").fetchone()["n"]
    c.close()
    return render_template("subscriptions.html", rows=rows, status=status, q=q,
                           counts=counts, counts_unpaid=counts_unpaid)


@app.route("/subscriptions/<sid>")
def subscription_detail(sid):
    c = conn()
    core.refresh_pause_statuses(c)        # flip any freeze that has just started/ended
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not sub:
        c.close(); abort(404)
    cust = c.execute("SELECT * FROM customers WHERE id=?", (sub["customer_id"],)).fetchone()
    dels = c.execute("SELECT * FROM deliveries WHERE subscription_id=? ORDER BY date DESC", (sid,)).fetchall()
    pauses = core.pauses_for(c, sid)
    slot_moves = core.slot_moves_for(c, sid)
    cur_pause = core.current_pause(c, sid)
    cur_move = core.current_slot_move(c, sid)
    comp = menu.sub_composition(c, sid)
    rules = menu.rules_for_sub(c, sid)
    rule_labels = {r["id"]: menu.rule_label(r) for r in rules}
    suggestions = agent.suggestions_for_sub(c, sub)
    components = menu.all_components(c)
    ctx = _schedule_ctx(c, sid)
    plans = c.execute("SELECT id,name FROM plans WHERE active=1 ORDER BY name").fetchall()
    schedule_summary = core.schedule_summary(c, sub)
    extras = c.execute("SELECT * FROM deliveries WHERE subscription_id=? AND is_extra=1 "
                       "ORDER BY date DESC", (sid,)).fetchall()
    # The next fortnight as the schedule will actually play out — the quickest way
    # to see that a plan does what was meant before the kitchen finds out.
    preview, d0 = [], date.today()
    for i in range(14):
        day_iso = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        if not core.is_operating_day(c, day_iso):
            continue
        p = core.plan_for_day(c, sub, day_iso)
        vname = next((v["name"] for v in ctx["variants"] if v["id"] == p["variant_id"]), None)
        preview.append({"date": day_iso, "deliver": p["deliver"], "slot": p["slot"],
                        "box": vname or (sub["plan_name"] or "Usual box"),
                        "frozen": core.is_paused_on(c, sid, day_iso)})
    c.close()
    return render_template("subscription_detail.html", sub=sub, cust=cust, dels=dels,
                           pauses=pauses, slot_moves=slot_moves, cur_pause=cur_pause,
                           cur_move=cur_move, comp=comp, rules=rules,
                           rule_labels=rule_labels, suggestions=suggestions,
                           components=components, fmt_qty=menu.fmt_qty,
                           plans=plans, schedule_summary=schedule_summary,
                           schedule_mode=core.schedule_mode(sub), preview=preview,
                           extras=extras,
                           today=date.today().strftime("%Y-%m-%d"), **ctx)


@app.route("/subscriptions/new", methods=["GET", "POST"])
def subscription_new():
    c = conn()
    if request.method == "POST":
        f = request.form
        sid = next_id(c, "subscriptions", "S")
        duration = f.get("duration", "Monthly")
        scheduled = int(f.get("total_meals_scheduled") or (5 if duration == "Weekly" else 20))
        price = float(f.get("subscription_price") or 0)
        fee = float(f.get("delivery_fee") or 0)
        received = float(f.get("amount_received") or 0)
        total = price + fee
        cust = c.execute("SELECT name FROM customers WHERE id=?", (f.get("customer_id"),)).fetchone()
        # Revenue is billed on the booking date (when the money/sale happened); the
        # subscription's clock starts on the start date. The month label follows the
        # booking date so the sale lands in the right revenue month.
        booking = f.get("booking_date") or f.get("start_date") or None
        bill_month = (datetime.strptime(booking, "%Y-%m-%d").strftime("%b-%Y")
                      if booking else core.month_label_iso())
        c.execute(
            """INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,duration,notes,
                 slot,preferred_timing,booking_date,start_date,month,subscription_price,
                 delivery_fee,total_amount,amount_received,payment_status,total_meals_scheduled,
                 status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, f.get("customer_id"), cust["name"] if cust else f.get("customer_name"),
             f.get("plan_name"), duration, f.get("notes"), f.get("slot", "Lunch"),
             f.get("preferred_timing"), booking,
             f.get("start_date") or None, bill_month,
             price, fee, total, received,
             "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid"),
             scheduled, "Active", db.now_iso()))
        c.commit()
        core.recompute_subscription(c, sid)
        # Composition and schedule BEFORE backfilling days: the generator reads
        # both, so setting them first means the very first orders it creates are
        # already on the right weekdays with the right box.
        _save_composition_from_form(c, sid, f)
        # Renewing? Carry the previous pack's contents, variants, schedule and
        # standing rules across, so a renewal starts as a copy of what the
        # customer was actually getting instead of an empty pack.
        renew_of = f.get("renew_of")
        vmap = None
        if renew_of:
            vmap = _copy_pack_setup(c, renew_of, sid,
                                    skip_composition=bool(f.get("comp_json")))
        _save_schedule_from_form(c, sid, f, vmap)
        # A back-dated start (meals already delivered, sub registered late) gets its
        # past days as Pending rows right away → they show in the backlog to mark.
        core.backfill_sub_days(c, sid)
        # If this booking came from converting a CRM lead, link them together.
        lead_id = f.get("lead")
        if lead_id:
            crm.link_subscription(c, lead_id, sid)
        c.close()
        flash(f"Subscription {sid} created.", "ok")
        return redirect(url_for("subscription_detail", sid=sid))
    customers = c.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY name").fetchall()
    menu_data = _menu_data(c)
    # "Renew" opens this form already filled in from the pack being renewed, so the
    # contents, add-ons and schedule are all there to check (and adjust, if this
    # month is different) before the new pack is created.
    renew_of = request.args.get("renew")
    src = (c.execute("SELECT * FROM subscriptions WHERE id=?", (renew_of,)).fetchone()
           if renew_of else None)
    existing_comp = ([{"id": r["component_id"], "qty": r["qty"], "kind": r["kind"],
                       "note": r["note"] or ""} for r in menu.sub_composition(c, src["id"])]
                     if src else [])
    ctx = _schedule_ctx(c, src["id"] if src else None)
    c.close()
    return render_template("subscription_form.html", customers=customers, plans=plans,
                           sub=src, today=date.today().strftime("%Y-%m-%d"),
                           preset_customer=request.args.get("customer"),
                           lead=request.args.get("lead"), menu_data=menu_data,
                           existing_comp=existing_comp, renew_of=(src["id"] if src else None),
                           is_new=True, **ctx)


@app.route("/subscriptions/<sid>/edit", methods=["GET", "POST"])
def subscription_edit(sid):
    c = conn()
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not sub:
        c.close(); abort(404)
    if request.method == "POST":
        f = request.form
        price = float(f.get("subscription_price") or 0)
        fee = float(f.get("delivery_fee") or 0)
        received = float(f.get("amount_received") or 0)
        total = price + fee
        # Allow re-pointing the subscription to a different customer (fixes a wrong
        # registration). When changed, keep customer_id + the denormalised name in sync.
        cust_id = f.get("customer_id") or sub["customer_id"]
        cust = c.execute("SELECT id,name FROM customers WHERE id=?", (cust_id,)).fetchone()
        booking = f.get("booking_date") or f.get("start_date") or sub["booking_date"]
        bill_month = (datetime.strptime(booking, "%Y-%m-%d").strftime("%b-%Y")
                      if booking else sub["month"])
        c.execute(
            """UPDATE subscriptions SET customer_id=?, customer_name=?, plan_name=?, duration=?,
                 notes=?, slot=?, preferred_timing=?, booking_date=?, start_date=?, month=?,
                 subscription_price=?, delivery_fee=?, total_amount=?, amount_received=?,
                 payment_status=?, total_meals_scheduled=? WHERE id=?""",
            (cust["id"] if cust else sub["customer_id"],
             cust["name"] if cust else sub["customer_name"],
             f.get("plan_name"), f.get("duration"), f.get("notes"), f.get("slot"),
             f.get("preferred_timing"), booking, f.get("start_date") or None, bill_month,
             price, fee, total, received,
             "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid"),
             int(f.get("total_meals_scheduled") or sub["total_meals_scheduled"] or 20), sid))
        c.commit()
        core.recompute_subscription(c, sid)
        # Start date may have been corrected backwards/forwards: create the missing
        # past-day rows (they land in the backlog) and drop impossible ones.
        core.backfill_sub_days(c, sid)
        # If the standing slot changed, re-point any still-Pending order rows (unless a
        # temporary slot move is overriding them) so today/future show the right slot.
        core.resync_order_slots(c, sid)
        # Keep the denormalised name on any future delivery rows correct too.
        c.execute("UPDATE deliveries SET name=?, customer_id=? WHERE subscription_id=?",
                  (cust["name"] if cust else sub["customer_name"],
                   cust["id"] if cust else sub["customer_id"], sid))
        c.commit()
        _save_composition_from_form(c, sid, f)
        _save_schedule_from_form(c, sid, f)
        c.close()
        flash(f"Subscription {sid} updated.", "ok")
        return redirect(url_for("subscription_detail", sid=sid))
    customers = c.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY name").fetchall()
    menu_data = _menu_data(c)
    existing_comp = [{"id": r["component_id"], "qty": r["qty"], "kind": r["kind"],
                      "note": r["note"] or ""} for r in menu.sub_composition(c, sid)]
    ctx = _schedule_ctx(c, sid)
    c.close()
    return render_template("subscription_form.html", customers=customers, plans=plans,
                           sub=sub, today=date.today().strftime("%Y-%m-%d"),
                           preset_customer=None, lead=None, menu_data=menu_data,
                           existing_comp=existing_comp, renew_of=None, is_new=False, **ctx)


@app.route("/subscriptions/<sid>/status", methods=["POST"])
def subscription_status(sid):
    # Pause/Resume now run through the dated pause-window routes below. This only
    # toggles the non-pause states (mark completed / re-activate).
    new = request.form.get("to")
    c = conn()
    if new in ("Active", "Completed"):
        c.execute("UPDATE subscriptions SET status=? WHERE id=?", (new, sid))
        c.commit()
        core.recompute_subscription(c, sid)
    c.close()
    flash(f"Subscription {sid} set to {new}.", "ok")
    return redirect(request.referrer or url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/pause", methods=["POST"])
def subscription_pause(sid):
    """Add / edit / remove a freeze window, or resume from a chosen date.

    Actions (form field `action`):
      add     – start (default today) + optional end (blank = open-ended) + reason
      update  – edit an existing window (`pid`, start, end, reason)
      delete  – drop a window (`pid`) so those days run normally again
      resume  – resume meals from `resume` date (default today); trims the freeze
    """
    c = conn()
    if not c.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        c.close(); abort(404)
    action = request.form.get("action", "add")
    today_iso = date.today().strftime("%Y-%m-%d")
    if action == "add":
        start = (request.form.get("start") or "").strip() or today_iso
        end = (request.form.get("end") or "").strip() or None
        if end and end < start:
            start, end = end, start
        core.add_pause(c, sid, start, end, request.form.get("reason", ""))
        flash("Subscription frozen." if not end
              else f"Frozen {fmt_date(start)} – {fmt_date(end)}.", "ok")
    elif action == "update":
        start = (request.form.get("start") or "").strip() or today_iso
        end = (request.form.get("end") or "").strip() or None
        if end and end < start:
            start, end = end, start
        core.update_pause(c, int(request.form["pid"]), start, end, request.form.get("reason", ""))
        flash("Freeze updated.", "ok")
    elif action == "delete":
        core.delete_pause(c, int(request.form["pid"]))
        flash("Freeze removed — those days run normally again.", "ok")
    elif action == "resume":
        resume = (request.form.get("resume") or "").strip() or today_iso
        core.resume_subscription(c, sid, resume)
        flash(f"Resumed from {fmt_date(resume)}.", "ok")
    c.close()
    return redirect(request.referrer or url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/slot-move", methods=["POST"])
def subscription_slot_move(sid):
    """Add / edit / remove a temporary slot move (e.g. Lunch → Dinner for a week)."""
    c = conn()
    if not c.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        c.close(); abort(404)
    action = request.form.get("action", "add")
    today_iso = date.today().strftime("%Y-%m-%d")
    if action == "add":
        slot = request.form.get("slot", "Dinner")
        start = (request.form.get("start") or "").strip() or today_iso
        end = (request.form.get("end") or "").strip() or None
        if end and end < start:
            start, end = end, start
        core.add_slot_move(c, sid, slot, start, end, request.form.get("reason", ""))
        flash(f"Moved to {slot}" + ("" if not end else f" until {fmt_date(end)}") + ".", "ok")
    elif action == "update":
        slot = request.form.get("slot", "Dinner")
        start = (request.form.get("start") or "").strip() or today_iso
        end = (request.form.get("end") or "").strip() or None
        if end and end < start:
            start, end = end, start
        core.update_slot_move(c, int(request.form["mid"]), slot, start, end,
                              request.form.get("reason", ""))
        flash("Slot move updated.", "ok")
    elif action == "delete":
        core.delete_slot_move(c, int(request.form["mid"]))
        flash("Slot move removed — back to the usual slot.", "ok")
    c.close()
    return redirect(request.referrer or url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/payment", methods=["POST"])
def subscription_payment(sid):
    c = conn()
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if sub:
        received = request.form.get("amount_received")
        received = float(received) if received not in (None, "") else (sub["total_amount"] or 0)
        total = sub["total_amount"] or 0
        ps = "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid")
        c.execute("UPDATE subscriptions SET amount_received=?, payment_status=? WHERE id=?",
                  (received, ps, sid))
        c.commit()
    c.close()
    flash("Payment updated.", "ok")
    return redirect(request.referrer or url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/renew", methods=["POST"])
def subscription_renew(sid):
    c = conn()
    old = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not old:
        c.close(); abort(404)
    nid = next_id(c, "subscriptions", "S")
    today_iso = date.today().strftime("%Y-%m-%d")
    # Booking (billing) date dates the revenue; start date starts the meal clock. Both
    # come from the renew form (default today) so a renewal can be billed on a chosen day.
    start = (request.form.get("start_date") or "").strip() or today_iso
    booking = (request.form.get("booking_date") or "").strip() or start
    # Carry over the received amount/payment only if asked (default: fresh unpaid pack).
    received = float(request.form.get("amount_received") or 0)
    total = old["total_amount"] or 0
    ps = "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid")
    c.execute(
        """INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,duration,notes,
             slot,preferred_timing,booking_date,start_date,month,subscription_price,delivery_fee,
             total_amount,amount_received,payment_status,total_meals_scheduled,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (nid, old["customer_id"], old["customer_name"], old["plan_name"], old["duration"],
         old["notes"], old["slot"], old["preferred_timing"], booking, start,
         core.month_label_iso(booking), old["subscription_price"], old["delivery_fee"],
         old["total_amount"], received, ps, old["total_meals_scheduled"], "Active", db.now_iso()))
    c.commit()
    # A renewal is the same customer eating the same thing next month, so it has to
    # arrive as a full copy: box contents, add-ons, alternative boxes, the weekly
    # schedule and the standing rules. Copying only the subscription row (which is
    # all this used to do) produced a pack with no contents, which then had to be
    # rebuilt by hand and silently broke the prep list until someone noticed.
    _copy_pack_setup(c, sid, nid)
    core.recompute_subscription(c, nid)
    core.backfill_sub_days(c, nid)      # renewals can be back-dated too
    c.close()
    flash(f"Renewed {sid} → new subscription {nid} (contents and schedule carried over).", "ok")
    return redirect(url_for("subscription_detail", sid=nid))


@app.route("/subscriptions/<sid>/delete", methods=["POST"])
def subscription_delete(sid):
    """Delete a subscription (e.g. created by mistake) and renumber the rest so the
    IDs stay gap-free. A safety backup is taken first so nothing is ever truly lost."""
    c = conn()
    sub = c.execute("SELECT customer_name FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not sub:
        c.close(); abort(404)
    try:
        import backup
        backup.make_backup(quiet=True)
    except Exception:
        pass                                  # never block a delete on backup trouble
    name = sub["customer_name"]
    core.delete_subscription(c, sid)
    c.close()
    flash(f"Deleted subscription for {name}. Remaining subscriptions renumbered.", "ok")
    return redirect(url_for("subscriptions"))


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
@app.route("/customers")
def customers():
    c = conn()
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM customers"
    args = []
    if q:
        sql += " WHERE name LIKE ? OR phone LIKE ? OR locality LIKE ?"
        args = [f"%{q}%"] * 3
    sql += " ORDER BY name"
    rows = c.execute(sql, args).fetchall()
    # attach active sub count
    out = []
    for r in rows:
        n = c.execute("SELECT COUNT(*) n FROM subscriptions WHERE customer_id=? "
                      "AND status IN ('Active','Ending Soon')", (r["id"],)).fetchone()["n"]
        out.append((r, n))
    analytics = list(reversed(core.customer_analytics(c)))   # newest month first
    c.close()
    return render_template("customers.html", rows=out, q=q, analytics=analytics)


@app.route("/customers/<cid>")
def customer_detail(cid):
    c = conn()
    cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        c.close(); abort(404)
    subs = c.execute("SELECT * FROM subscriptions WHERE customer_id=? ORDER BY id DESC", (cid,)).fetchall()
    spent = c.execute("SELECT COALESCE(SUM(amount_received),0) s FROM subscriptions WHERE customer_id=?",
                      (cid,)).fetchone()["s"]
    addrs = []
    for a in ops.customer_addresses(c, cid):
        km = src = None
        if a["lat"] is not None:
            km, src = ops.road_km_from_kitchen(c, a["lat"], a["lng"])
        addrs.append({"a": a, "km": round(km, 1) if km else None, "src": src,
                      "zone": ops.zone_for_km(c, km) if km else None,
                      "days": ops.weekday_label(a["weekdays"]),
                      "wd": ops.weekday_list(a["weekdays"])})
    c.close()
    return render_template("customer_detail.html", cust=cust, subs=subs, spent=spent,
                           addrs=addrs, weekdays=ops.WEEKDAYS,
                           corridors=ops.CORRIDORS)


def _parse_pin(text):
    """(lat, lng) from a plus code, Maps link, coordinate pair or place name.
    See ops.resolve_location."""
    lat, lng, _ = ops.resolve_location(text)
    return lat, lng


def _flash_pin_result(raw, lat, how):
    """Say out loud what happened to a pasted pin.

    Both halves matter. A pin that could not be read used to be dropped in
    silence, so the customer stayed unpinned and nothing said why. And a pin
    found from a place NAME is a guess — an accurate-looking one — so it is
    announced with the address it matched, because a wrong pin quietly bills the
    petrol allowance against the wrong distance every day it survives.
    """
    if not (raw or "").strip():
        return
    if lat is None:
        flash(f"The map pin wasn't saved — {how}.", "")
    elif (how or "").startswith("found by name"):
        flash(f"Map pin {how}. Check it on the map if that isn't the right place.", "ok")


def _save_customer_geo(c, f, cid):
    """Save the map pin and delivery hints. Shared by new + edit.

    The pin is written to the customer's DEFAULT ADDRESS as well as to the
    customer row. Every map screen reads the address — so writing only the
    customer row, as this used to, meant a pin saved here changed nothing at all:
    the customer stayed on the "needs a pin" list, never showed a distance, and
    rode the evening route as unpinned.
    """
    lat, lng, how = ops.resolve_location(f.get("pin"))
    _flash_pin_result(f.get("pin"), lat, how)
    if lat is None and (f.get("lat") or "").strip():
        try:
            lat, lng = float(f.get("lat")), float(f.get("lng"))
        except (TypeError, ValueError):
            lat = lng = None
    if lat is not None:
        c.execute("UPDATE customers SET lat=?, lng=? WHERE id=?", (lat, lng, cid))
    corridor = (f.get("corridor") or "").strip() or None
    drop_type = (f.get("drop_type") or "").strip() or None
    map_note = (f.get("map_note") or "").strip() or None
    c.execute("UPDATE customers SET corridor=?, drop_type=?, map_note=? WHERE id=?",
              (corridor, drop_type, map_note, cid))
    aid = db.ensure_address_row(c, cid)      # a customer with no address row is invisible
    if aid:
        if lat is not None:
            c.execute("UPDATE customer_addresses SET lat=?, lng=? WHERE id=?", (lat, lng, aid))
        if (f.get("address") or "").strip():
            c.execute("UPDATE customer_addresses SET address=? WHERE id=?",
                      ((f.get("address") or "").strip(), aid))
        c.execute("UPDATE customer_addresses SET corridor=?, drop_type=?, map_note=? WHERE id=?",
                  (corridor, drop_type, map_note, aid))
    return lat is not None


@app.route("/customers/new", methods=["GET", "POST"])
def customer_new():
    c = conn()
    if request.method == "POST":
        f = request.form
        cid = next_id(c, "customers", "C")
        c.execute("INSERT INTO customers(id,name,phone,email,locality,address,notes,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (cid, f.get("name"), f.get("phone"),
                   (f.get("email") or "").strip().lower() or None, f.get("locality"),
                   f.get("address"), f.get("notes"), db.now_iso()))
        _save_customer_geo(c, f, cid)
        c.commit(); c.close()
        flash(f"Customer {cid} added.", "ok")
        return redirect(url_for("customer_detail", cid=cid))
    c.close()
    return render_template("customer_form.html", cust=None, corridors=ops.CORRIDORS)


@app.route("/customers/<cid>/edit", methods=["GET", "POST"])
def customer_edit(cid):
    c = conn()
    cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        c.close(); abort(404)
    if request.method == "POST":
        f = request.form
        c.execute("UPDATE customers SET name=?,phone=?,email=?,locality=?,address=?,notes=? "
                  "WHERE id=?",
                  (f.get("name"), f.get("phone"),
                   (f.get("email") or "").strip().lower() or None,
                   f.get("locality"), f.get("address"), f.get("notes"), cid))
        pinned = _save_customer_geo(c, f, cid)
        c.commit(); c.close()
        if (f.get("pin") or "").strip() and not pinned:
            flash("Customer updated — but that pin wasn't readable, so it was ignored. "
                  "Paste coordinates like 12.901234, 80.227891.", "")
        else:
            flash("Customer updated.", "ok")
        return redirect(url_for("customer_detail", cid=cid))
    c.close()
    return render_template("customer_form.html", cust=cust, corridors=ops.CORRIDORS)


# ---------------------------------------------------------------------------
# Subscription audit — find compositions the kitchen can't see
# ---------------------------------------------------------------------------
@app.route("/subscriptions/review")
def subscriptions_review():
    """Every live subscription checked against the current pack structure.

    The prep list can only count what is recorded as components, so an extra
    typed into the plan name never reaches the kitchen. This page surfaces those,
    worst first, and lets each one be fixed without leaving the list.
    """
    c = conn()
    rows = menu.audit_all(c)
    comps = menu.all_components(c)
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY id").fetchall()
    counts = {
        "high": sum(1 for r in rows if r["high"]),
        "any": sum(1 for r in rows if r["issues"]),
        "clean": sum(1 for r in rows if not r["issues"]),
    }
    c.close()
    return render_template("subscriptions_review.html", rows=rows, comps=comps,
                           plans=plans, counts=counts)


@app.route("/subscriptions/<sid>/apply-pack", methods=["POST"])
def subscription_apply_pack(sid):
    """Reset a subscription's base to the chosen pack's recipe, keeping add-ons."""
    c = conn()
    pid = request.form.get("plan_id", type=int)
    plan = c.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone() if pid else None
    if not plan:
        c.close()
        flash("Pick a pack first.", "")
        return redirect(url_for("subscriptions_review"))
    keep = [(a["component_id"], float(a["qty"]), a["note"] or "")
            for a in menu.sub_composition(c, sid) if a["kind"] == "addon"]
    base = [(r["component_id"], float(r["qty"]), r["note"] or "")
            for r in menu.plan_recipe(c, pid)]
    menu.save_sub_composition(c, sid, base, keep)
    if request.form.get("set_name"):
        c.execute("UPDATE subscriptions SET plan_name=? WHERE id=?", (plan["name"], sid))
    c.commit(); c.close()
    flash(f"Applied the {plan['name']} recipe.", "ok")
    return redirect(request.referrer or url_for("subscriptions_review"))


@app.route("/subscriptions/<sid>/addon", methods=["POST"])
def subscription_addon(sid):
    """Add (or clear) one add-on component on a subscription, from the review list."""
    c = conn()
    cid = request.form.get("component_id", type=int)
    qty = request.form.get("qty", type=float)
    existing = menu.sub_composition(c, sid)
    base = [(x["component_id"], float(x["qty"]), x["note"] or "")
            for x in existing if x["kind"] != "addon"]
    addons = [(x["component_id"], float(x["qty"]), x["note"] or "")
              for x in existing if x["kind"] == "addon" and x["component_id"] != cid]
    if cid and qty and qty > 0:
        addons.append((cid, qty, (request.form.get("note") or "").strip()))
    menu.save_sub_composition(c, sid, base, addons)
    c.commit(); c.close()
    flash("Add-on saved. It is now counted in the prep list.", "ok")
    return redirect(request.referrer or url_for("subscriptions_review"))


# ---------------------------------------------------------------------------
# Shift sheet — the printed page the evening runs on
# ---------------------------------------------------------------------------
@app.route("/sheet")
def sheet_pick():
    """Small chooser so the sheet is reachable without typing a URL."""
    d = today_iso()
    return redirect(url_for("sheet", d=d, slot="Dinner"))


@app.route("/sheet/<d>/<slot>")
def sheet(d, slot):
    """One slot's complete working sheet: what to cook, what goes in each box,
    where it goes, and the close-out. Designed for A4 paper and a pen —
    `?lang=ta` renders the whole thing in Tamil, `?print=1` opens the print dialog.
    """
    slot = "Dinner" if (slot or "").lower().startswith("d") else "Lunch"
    lang = "ta" if request.args.get("lang") == "ta" else "en"
    batch = request.args.get("batch", type=int)
    c = conn()

    rows = core.deliveries_for_day(c, d)
    prep = menu.prep_for_day(c, d, rows=rows)
    # `batch` prints one rider's run. The prep list deliberately stays whole-slot:
    # the kitchen cooks everything once, however many people carry it out.
    route = ops.build_route(c, d, slot, rows=rows, batch=batch)

    # Every route for this slot, so the sheet can offer a switcher. Without this
    # the split is invisible: you open the sheet, see all nine orders, and have
    # no way to know two runs exist or how to reach them.
    #
    # Worked out from the orders themselves, not from saved batch rows. Read from
    # `route_batches` this was empty for any day the Routes screen had not been
    # opened for, so the switcher vanished and the only sheet on offer was the
    # whole slot -- Chrompet and Semmancheri on one page, which is two directions
    # and two riders pretending to be one run.
    batches = ops.batches_on_day(c, d, slot, rows=rows)

    batch_row = None
    unrouted = []
    if batch is not None:
        ops.record_estimate(c, d, slot, batch, route)
        batch_row = c.execute(
            "SELECT * FROM route_batches WHERE date=? AND slot=? AND batch_no=?",
            (d, slot, batch)).fetchone()
        batch_row = dict(batch_row) if batch_row else {}
        # Fall back to the corridor's own name, so the page is headed "Tambaram
        # side" rather than a bare number that means nothing to whoever rides it.
        if not (batch_row.get("name") or "").strip() or                 batch_row.get("name") == f"Route {batch}":
            batch_row["name"] = ops.route_label(c, batch)
        # An order nobody has put on a route would otherwise appear on no sheet
        # at all. It still has to be cooked and delivered, so it is printed here
        # as a warning rather than silently dropped.
        routed = set()
        whole = ops.build_route(c, d, slot, rows=rows, split=False)
        for grp in (whole["stops"], whole["porter_direct"], whole["unpinned"]):
            for st in grp:
                if st.get("delivery_id") and st.get("batch_no") is not None:
                    routed.add(st["delivery_id"])
        for r in rows:
            if (r["meal_type"] or "Lunch") != slot:
                continue
            if r["status"] not in ("Pending", "Delivered"):
                continue
            if r["id"] not in routed:
                unrouted.append(r["name"])

    slot_key = "dinner" if slot == "Dinner" else "lunch"
    prep_slot = prep[slot_key]

    # Per-order cards, in the same sequence the rider will ride.
    # Every order needs its stop, whichever list it ended up in: ridden, handed
    # to a Porter at a point, booked direct, or still unpinned. Looking only at
    # `stops` would print a handed-over order as though it had no location.
    by_delivery = {}
    for group in (route["stops"], route["handoffs"], route["porter_direct"],
                  route["unpinned"]):
        for s in group:
            if s.get("delivery_id"):
                by_delivery[s["delivery_id"]] = s
    cache, plan_cache = {}, {}
    cards, no_comp = [], []
    ordered_rows = sorted(
        [r for r in rows
         if (r["meal_type"] or "Lunch") == slot and r["status"] in ("Pending", "Delivered")
         # printing one run shows only that run's orders
         and (batch is None or r["id"] in by_delivery)],
        key=lambda r: (by_delivery.get(r["id"], {}).get("seq") or 999))
    for r in ordered_rows:
        items, label = menu.order_items(c, r, d, cache, plan_cache)
        stop = by_delivery.get(r["id"])
        card = {
            "seq": (stop or {}).get("seq"),
            "name": r["name"], "phone": r["phone"],
            # The address resolved for THIS day — office, home, or a one-off. The
            # label is only worth printing when the customer actually has more
            # than one; otherwise "[Home]" on every card is just noise.
            "address": (stop["address"] if stop else r["address"]) or "",
            "address_label": (stop["address_label"] if stop else None)
                             if (r["n_addresses"] or 0) > 1 else None,
            "address_switched": bool(r["address_id"]),
            "locality": r["locality"] or "",
            "map_note": (stop["map_note"] if stop else r["map_note"]),
            "plan": label or "Meal",
            "items": [{"name": sheet_i18n.component_name(i["name"], lang),
                       "qty": menu.fmt_qty(i["qty"], i["unit"]),
                       "note": i.get("note")} for i in items],
            # The one line that loses a customer if it's missed.
            "note": " · ".join(x for x in [(r["sub_notes"] or "").strip(),
                                           (r["day_note"] or "").strip()] if x),
            "stop": by_delivery.get(r["id"]),
        }
        cards.append(card)
        if not items:
            no_comp.append(r["name"])

    comps = [{"name": sheet_i18n.component_name(x["name"], lang), "emoji": x["emoji"],
              "display": x["display"], "breakdown": x["breakdown"], "notes": x["notes"]}
             for x in prep_slot["components"]]

    ov = _sheet_overrides(c, d, slot, lang)
    timings = {
        "prep":   ops.setting(c, "prep_start_time") or "4:00 PM",
        "pack":   ops.setting(c, "pack_done_time") or "6:15 PM",
        "depart": _ampm(ops.setting(c, "depart_time")),
        "done":   _ampm(ops.setting(c, "shift_end")),
    }
    staff = request.args.get("staff") or ops.setting(c, "staff_name") or ""
    kitchen_addr = ops.setting(c, "kitchen_address")
    c.close()
    return render_template(
        "sheet.html", d=d, slot=slot, lang=lang, T=sheet_i18n.t(lang),
        comps=comps, cards=cards, route=route, no_comp=no_comp, ov=ov,
        qr=ops.qr_data_uri(route["maps_url"]), timings=timings,
        staff=staff, kitchen_addr=kitchen_addr,
        batch=batch, batch_row=batch_row, unrouted=unrouted, batches=batches,
        day_label=fmt_date(d, "%A, %d %b %Y"),
        autoprint=request.args.get("print") == "1")


def _ampm(hhmm):
    """'21:00' → '9:00 PM'. Written out because %-I/%#I differ per platform."""
    try:
        h, m = str(hhmm).split(":")
        h, m = int(h), int(m)
        return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    except (ValueError, AttributeError):
        return str(hhmm)


def _sheet_overrides(c, d, slot, lang):
    """Hand edits saved against this sheet, as {element_key: html}."""
    row = c.execute("SELECT overrides FROM shifts WHERE date=? AND slot=?", (d, slot)).fetchone()
    if not row or not row["overrides"]:
        return {}
    try:
        return json.loads(row["overrides"]).get(lang, {})
    except (ValueError, AttributeError):
        return {}


@app.route("/sheet/<d>/<slot>/save", methods=["POST"])
def sheet_save(d, slot):
    """Persist hand edits made to a sheet before it goes out.

    Stored per language, keyed by element, and applied on top of freshly generated
    data — so regenerating tomorrow's sheet never inherits today's corrections, and
    editing a printed quantity never silently rewrites the actual order.
    """
    import re
    slot = "Dinner" if (slot or "").lower().startswith("d") else "Lunch"
    lang = "ta" if request.args.get("lang") == "ta" else "en"
    try:
        edits = json.loads(request.form.get("edits") or "{}")
    except ValueError:
        return jsonify({"ok": False, "err": "bad payload"}), 400
    # The content is the owner's own, but strip anything executable so a stray
    # paste can never break every future render of this sheet.
    clean = {}
    for k, v in list(edits.items())[:400]:
        v = re.sub(r"(?is)<(script|style|iframe)\b.*?</\1>", "", str(v))[:8000]
        v = re.sub(r"(?i)\son\w+\s*=\s*\"[^\"]*\"", "", v)
        clean[str(k)[:120]] = v

    c = conn()
    row = c.execute("SELECT id, overrides FROM shifts WHERE date=? AND slot=?",
                    (d, slot)).fetchone()
    blob = {}
    if row and row["overrides"]:
        try:
            blob = json.loads(row["overrides"])
        except ValueError:
            blob = {}
    blob[lang] = clean
    if row:
        c.execute("UPDATE shifts SET overrides=?, updated_at=? WHERE id=?",
                  (json.dumps(blob), db.now_iso(), row["id"]))
    else:
        c.execute("INSERT INTO shifts(date, slot, overrides, created_at, updated_at) "
                  "VALUES(?,?,?,?,?)", (d, slot, json.dumps(blob), db.now_iso(), db.now_iso()))
    c.commit(); c.close()
    return jsonify({"ok": True, "n": len(clean)})


@app.route("/sheet/<d>/<slot>/reset", methods=["POST"])
def sheet_reset(d, slot):
    """Drop hand edits and go back to exactly what the app generated."""
    slot = "Dinner" if (slot or "").lower().startswith("d") else "Lunch"
    c = conn()
    c.execute("UPDATE shifts SET overrides=NULL WHERE date=? AND slot=?", (d, slot))
    c.commit(); c.close()
    flash("Sheet reset to the generated version.", "ok")
    return redirect(url_for("sheet", d=d, slot=slot, lang=request.args.get("lang", "en")))


# ---------------------------------------------------------------------------
# Customer addresses — home / office / gym, with an optional weekday schedule
# ---------------------------------------------------------------------------
@app.route("/customers/<cid>/schedule")
def customer_schedule(cid):
    """The next two weeks of this customer's deliveries, with the address each
    will go to and a way to change any of them ahead of time.

    This is the answer to "I'll tell you the day before" — the change is recorded
    against that specific future delivery, so whoever prints the sheet that
    evening gets the right address without having to remember the conversation.
    """
    c = conn()
    cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        c.close(); abort(404)
    days = int(request.args.get("days", 14))
    start = date.today()
    addrs = ops.customer_addresses(c, cid)
    acache = {cid: addrs}
    rows = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        if not core.is_operating_day(c, d):
            continue
        for dl in c.execute(
                "SELECT * FROM deliveries WHERE customer_id=? AND date=? "
                "AND status IN ('Pending','Delivered') ORDER BY (meal_type='Dinner')",
                (cid, d)).fetchall():
            slot = dl["meal_type"] or "Lunch"
            chosen = ops.resolve_address(c, cid, d, dl["address_id"], acache, slot)
            auto = ops.resolve_address(c, cid, d, None, acache, slot)
            rows.append({
                "id": dl["id"], "date": d, "slot": slot, "status": dl["status"],
                "label": fmt_date(d, "%a %d %b"),
                "addr": chosen, "auto": auto,
                "overridden": bool(dl["address_id"]),
                "is_today": d == start.strftime("%Y-%m-%d"),
            })
    c.close()
    return render_template("customer_schedule.html", cust=cust, rows=rows,
                           addrs=addrs, days=days)


@app.route("/customers/<cid>/address", methods=["POST"])
def customer_address_save(cid):
    """Add or update one of a customer's delivery addresses."""
    c = conn()
    f = request.form
    aid = f.get("address_id", type=int)
    lat, lng, how = ops.resolve_location(f.get("pin"))
    weekdays = ",".join(sorted(f.getlist("weekdays")))
    fields = ((f.get("label") or "").strip() or "Address",
              (f.get("address") or "").strip(),
              (f.get("drop_type") or "").strip() or None,
              (f.get("map_note") or "").strip() or None,
              (f.get("corridor") or "").strip() or None,
              weekdays or None,
              (f.get("slot") or "").strip() or None)

    if aid:
        c.execute("UPDATE customer_addresses SET label=?, address=?, drop_type=?, "
                  "map_note=?, corridor=?, weekdays=?, slot=? WHERE id=? AND customer_id=?",
                  (*fields, aid, cid))
        if lat is not None:
            c.execute("UPDATE customer_addresses SET lat=?, lng=? WHERE id=?", (lat, lng, aid))
    else:
        first = not c.execute("SELECT 1 FROM customer_addresses WHERE customer_id=?",
                              (cid,)).fetchone()
        cur = c.execute(
            "INSERT INTO customer_addresses(customer_id,label,address,drop_type,map_note,"
            "corridor,weekdays,slot,lat,lng,is_default,active,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)",
            (cid, *fields, lat, lng, 1 if first else 0, db.now_iso()))
        aid = cur.lastrowid
    c.commit()
    if c.execute("SELECT is_default FROM customer_addresses WHERE id=?",
                 (aid,)).fetchone()["is_default"]:
        ops.set_default_address(c, cid, aid)      # keep the mirror on customers in step
    c.close()
    if (f.get("pin") or "").strip() and lat is None:
        flash(f"Address saved, but the pin wasn't readable — {how}.", "")
    elif (how or "").startswith("found by name"):
        flash(f"Address saved — pin {how}. Check it if that isn't the right place.", "ok")
    else:
        flash("Address saved.", "ok")
    return redirect(url_for("customer_detail", cid=cid))


@app.route("/customers/<cid>/address/<int:aid>/default", methods=["POST"])
def customer_address_default(cid, aid):
    c = conn()
    ops.set_default_address(c, cid, aid)
    c.close()
    flash("Default address updated.", "ok")
    return redirect(url_for("customer_detail", cid=cid))


@app.route("/customers/<cid>/address/<int:aid>/archive", methods=["POST"])
def customer_address_archive(cid, aid):
    """Hide an address without deleting it — past deliveries still reference it."""
    c = conn()
    c.execute("UPDATE customer_addresses SET active=0, is_default=0 "
              "WHERE id=? AND customer_id=?", (aid, cid))
    c.commit()
    left = ops.customer_addresses(c, cid)
    if left and not any(a["is_default"] for a in left):
        ops.set_default_address(c, cid, left[0]["id"])
    c.close()
    flash("Address archived. Past deliveries keep their record of it.", "ok")
    return redirect(url_for("customer_detail", cid=cid))


@app.route("/api/delivery/<int:did>/address", methods=["POST"])
def api_delivery_address(did):
    """Send just this one day's delivery to a different address."""
    c = conn()
    aid = request.form.get("address_id", type=int)
    c.execute("UPDATE deliveries SET address_id=? WHERE id=?", (aid or None, did))
    c.commit()
    row = c.execute("SELECT * FROM deliveries WHERE id=?", (did,)).fetchone()
    addr = ops.resolve_address(c, row["customer_id"], row["date"], row["address_id"])
    c.close()
    return jsonify({"ok": True,
                    "label": addr["label"] if addr else None,
                    "address": (addr["address"] if addr else "") or "",
                    "override": bool(aid)})


# ---------------------------------------------------------------------------
# Route batches — splitting a slot between riders
# ---------------------------------------------------------------------------
@app.route("/routes")
def routes_board():
    """Split one slot into several runs, one per rider.

    Ten lunch orders are not one person's run: Tambaram and Sholinganallur are
    opposite directions. Each batch gets its own route, its own km and its own
    ₹3/km allowance, because two people riding two directions must be paid on
    their own distance.
    """
    c = conn()
    day = request.args.get("d") or today_iso()
    slot = "Dinner" if request.args.get("slot") == "Dinner" else "Lunch"
    n = request.args.get("n", type=int) or None

    saved = ops.batch_rows(c, day, slot)
    if n is None:
        n = max(len(saved), 2)
    n = max(1, min(n, 6))
    ops.ensure_batches(c, day, slot, n)
    saved = ops.batch_rows(c, day, slot)

    whole = ops.build_route(c, day, slot, split=False)
    orders = []
    for s in (whole["stops"] + whole["handoffs"] + whole["porter_direct"]
              + whole["unpinned"]):
        if s.get("is_handoff_point") or not s.get("delivery_id"):
            continue
        orders.append({
            "delivery_id": s["delivery_id"], "name": s["name"],
            "locality": s["locality"], "plan": s["plan_name"],
            "km": s.get("km"), "zone": s.get("zone"),
            "corridor": s.get("corridor"), "pinned": s.get("pinned"),
            "mode": s["mode"], "batch_no": s.get("batch_no"),
            "batch_from": s.get("batch_from"),
            "standing": s.get("standing_batch"), "address_id": s.get("address_id"),
        })
    orders.sort(key=lambda o: ((o["corridor"] or "zz"), -(o["km"] or 0),
                               o["name"].lower()))

    # Per-batch route, so each rider's km and allowance are their own.
    per = []
    for b in saved:
        r = ops.build_route(c, day, slot, batch=b["batch_no"])
        # Stamp the figures on the batch row. The allowance is handed over in cash
        # at the end of the week, and until now nothing wrote down what a run
        # measured — so Friday's number came out of memory.
        ops.record_estimate(c, day, slot, b["batch_no"], r)
        per.append({"b": b, "route": r,
                    "n": len(r["stops"]) + len(r["porter_direct"]) + len(r["unpinned"])})
    riders = [r["rider"] for r in c.execute(
        "SELECT DISTINCT rider FROM route_batches WHERE rider IS NOT NULL "
        "AND rider != '' ORDER BY rider")]
    unassigned = sum(1 for o in orders if not o["batch_no"])
    rate = ops.fnum(c, "petrol_rate")
    cmap = ops.corridor_routes(c)
    labels = {b["batch_no"]: ops.route_label(c, b["batch_no"]) for b in saved}
    c.close()
    return render_template("routes.html", day=day, slot=slot, n=n, orders=orders,
                           per=per, riders=riders, unassigned=unassigned,
                           rate=rate, whole=whole, cmap=cmap, labels=labels,
                           corridors=ops.CORRIDORS)


@app.route("/routes/suggest", methods=["POST"])
def routes_suggest():
    """Propose a split by direction. Standing choices are kept, not overwritten."""
    c = conn()
    day = request.form.get("d") or today_iso()
    slot = "Dinner" if request.form.get("slot") == "Dinner" else "Lunch"
    n = max(1, min(request.form.get("n", type=int) or 2, 6))
    ops.ensure_batches(c, day, slot, n)
    plan = ops.suggest_batches(c, day, slot, n)
    for did, b in plan.items():
        c.execute("UPDATE deliveries SET batch_no=? WHERE id=?", (b, did))
    c.commit(); c.close()
    flash(f"Suggested a split into {n} routes. Adjust anything, then save.", "ok")
    return redirect(url_for("routes_board", d=day, slot=slot, n=n))


@app.route("/routes/save", methods=["POST"])
def routes_save():
    c = conn()
    f = request.form
    day = f.get("d") or today_iso()
    slot = "Dinner" if f.get("slot") == "Dinner" else "Lunch"
    n = max(1, min(f.get("n", type=int) or 2, 6))

    for i in range(1, n + 1):
        c.execute("UPDATE route_batches SET name=?, rider=?, actual_km=? "
                  "WHERE date=? AND slot=? AND batch_no=?",
                  ((f.get(f"bname_{i}") or "").strip() or f"Route {i}",
                   (f.get(f"rider_{i}") or "").strip() or None,
                   f.get(f"actual_{i}", type=float),
                   day, slot, i))
    moved = standing = 0
    for key in f:
        if not key.startswith("b_"):
            continue
        did = key[2:]
        raw = (f.get(key) or "").strip()
        val = None if raw == "" else max(1, min(int(raw), n))
        cur = c.execute("SELECT batch_no FROM deliveries WHERE id=?", (did,)).fetchone()
        if cur is None:
            continue
        if cur["batch_no"] != val:
            c.execute("UPDATE deliveries SET batch_no=? WHERE id=?", (val, did))
            moved += 1
        if f.get(f"always_{did}"):
            aid = f.get(f"addr_{did}", type=int)
            if aid:
                c.execute("UPDATE customer_addresses SET batch_no=? WHERE id=?",
                          (val, aid))
                standing += 1
    c.commit(); c.close()
    bits = []
    if moved:
        bits.append(f"{moved} order{'s' if moved != 1 else ''} moved")
    if standing:
        bits.append(f"{standing} set for every delivery")
    flash(", ".join(bits) + "." if bits else "Routes saved.", "ok")
    return redirect(url_for("routes_board", d=day, slot=slot, n=n))


@app.route("/routes/corridors", methods=["POST"])
def routes_corridors():
    """Set which run each direction rides on, once, for every day after.

    Assigning ten orders to routes by hand every morning is a job nobody keeps
    doing, and the day it is skipped the sheet silently goes back to one mixed
    run. A locality already resolves to a corridor, so the corridor holds the
    answer and a customer added tomorrow is on the right route immediately.
    """
    c = conn()
    f = request.form
    day = f.get("d") or today_iso()
    slot = "Dinner" if f.get("slot") == "Dinner" else "Lunch"
    m = {}
    for corr in ops.CORRIDORS:
        raw = (f.get(f"corr_{corr}") or "").strip()
        if raw:
            try:
                v = int(raw)
            except ValueError:
                continue
            if 1 <= v <= 6:
                m[corr] = v
    db.set_setting(c, "corridor_routes", json.dumps(m))
    if m:
        ops.ensure_batches(c, day, slot, max(m.values()))
    c.commit(); c.close()
    flash("Saved. Every order now rides the route its direction is set to, "
          "unless you say otherwise for a customer or a day.", "ok")
    return redirect(url_for("routes_board", d=day, slot=slot))


@app.route("/routes/payout")
def routes_payout():
    """What the week's riding owes, per rider — the sheet the cash is counted from.

    Salary is monthly and conveyance is weekly, so this deliberately covers a
    Monday-to-Sunday week and nothing else. Kilometres are the rider's own
    corrected figure where he gave one; otherwise the measured route, marked as
    such. The kitchen -> stops -> kitchen loop is what is measured, so the ride
    back is already inside every number on this page.
    """
    c = conn()
    anchor_day = request.args.get("d") or today_iso()
    try:
        ad = datetime.strptime(anchor_day, "%Y-%m-%d").date()
    except ValueError:
        ad = date.today()
    start = ad - timedelta(days=ad.weekday())          # Monday of that week
    end = start + timedelta(days=6)
    data = ops.payout_week(c, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    c.close()
    return render_template(
        "payout.html", data=data,
        start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
        label=f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}",
        prev_d=(start - timedelta(days=7)).strftime("%Y-%m-%d"),
        next_d=(start + timedelta(days=7)).strftime("%Y-%m-%d"),
        today_str=today_iso())


# ---------------------------------------------------------------------------
# Porter handoff points
# ---------------------------------------------------------------------------
@app.route("/porter-points")
def porter_points():
    """The handoff spots where a far order is passed to a Porter.

    The saving is real: a Porter booked from the kitchen to Adyar runs ~19 km,
    but if the rider carries it to Sholinganallur junction — a stop he passes
    anyway — the Porter only runs the remainder. Cost falls, the rider loses
    nothing.
    """
    c = conn()
    klat, klng, kok = ops.kitchen(c)
    rows = []
    for p in c.execute("SELECT * FROM porter_points ORDER BY active DESC, sort, id"):
        km = src = None
        if p["lat"] is not None:
            km, src = ops.road_km_from_kitchen(c, p["lat"], p["lng"])
        rows.append({"p": p, "km": round(km, 1) if km else None,
                     "serves": [x.strip() for x in (p["serves"] or "").split(",") if x.strip()]})
    # Which far stops currently have nowhere to hand off — the gap worth filling.
    uncovered = {}
    for d in (today_iso(), (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")):
        for slot in ("Lunch", "Dinner"):
            r = ops.build_route(c, d, slot)
            for s in r["porter_direct"]:
                uncovered.setdefault(s["corridor"], set()).add(s["name"])

    # --- the assignment board: one day + slot, every order, who takes it --------
    day = request.args.get("d") or today_iso()
    slot = "Dinner" if request.args.get("slot") == "Dinner" else "Lunch"
    route = ops.build_route(c, day, slot)
    board = []
    for s in (route["stops"] + route["handoffs"] + route["porter_direct"]
              + route["unpinned"]):
        if s.get("is_handoff_point") or not s.get("delivery_id"):
            continue                      # the grouped point row is not an order
        board.append({
            "delivery_id": s["delivery_id"],
            "name": s["name"],
            "locality": s["locality"],
            "plan": s["plan_name"],
            "km": s.get("km"),
            "zone": s.get("zone"),
            "corridor": s.get("corridor"),
            "pinned": s.get("pinned"),
            "mode": s["mode"],
            "point": s.get("porter_point"),
            "assigned": bool(s.get("assigned")),
            "assigned_from": s.get("assigned_from"),
            "address_id": s.get("address_id"),
        })
    board.sort(key=lambda b: (-(b["km"] or 0), b["name"].lower()))
    c.close()
    return render_template("porter_points.html", rows=rows, corridors=ops.CORRIDORS,
                           kitchen_ok=kok, klat=klat, klng=klng,
                           uncovered={k: sorted(v) for k, v in uncovered.items()},
                           board=board, day=day, slot=slot,
                           points=[r["p"] for r in rows if r["p"]["active"]])


@app.route("/porter-points/assign", methods=["POST"])
def porter_points_assign():
    """Say who hands over where, for one day's orders.

    Deliberately one form for the whole list: assigning four orders to a point
    should be four taps and one save, not four round trips. The customer's
    address is never touched — this only decides where the bag changes hands.
    """
    c = conn()
    f = request.form
    day = f.get("d") or today_iso()
    slot = "Dinner" if f.get("slot") == "Dinner" else "Lunch"
    changed = standing = 0
    for key in f:
        if not key.startswith("pp_"):
            continue
        did = key[3:]
        raw = (f.get(key) or "").strip()
        # "" = decide automatically, "rider" = he takes it, otherwise a point id
        val = None if raw == "" else (0 if raw == "rider" else int(raw))
        cur = c.execute("SELECT porter_point_id FROM deliveries WHERE id=?",
                        (did,)).fetchone()
        if cur is None:
            continue
        if (cur["porter_point_id"] if cur["porter_point_id"] is not None else None) != val:
            c.execute("UPDATE deliveries SET porter_point_id=? WHERE id=?", (val, did))
            changed += 1
        # "every time" — store the same choice on the address this order goes to
        if f.get(f"always_{did}"):
            aid = f.get(f"addr_{did}", type=int)
            if aid:
                c.execute("UPDATE customer_addresses SET porter_point_id=? WHERE id=?",
                          (val, aid))
                standing += 1
    c.commit(); c.close()
    bits = []
    if changed:
        bits.append(f"{changed} order{'s' if changed != 1 else ''} updated")
    if standing:
        bits.append(f"{standing} set for every delivery")
    flash(", ".join(bits) + "." if bits else "Nothing changed.", "ok" if bits else "")
    return redirect(url_for("porter_points", d=day, slot=slot))


@app.route("/porter-points/save", methods=["POST"])
def porter_point_save():
    c = conn()
    f = request.form
    pid = f.get("id", type=int)
    lat, lng, how = ops.resolve_location(f.get("pin"))
    serves = ",".join(f.getlist("serves"))
    vals = ((f.get("code") or "").strip() or None,
            (f.get("name") or "").strip(),
            serves or None,
            (f.get("note") or "").strip() or None,
            1 if f.get("active") else 0)
    if pid:
        c.execute("UPDATE porter_points SET code=?, name=?, serves=?, note=?, active=? "
                  "WHERE id=?", (*vals, pid))
        if lat is not None:
            c.execute("UPDATE porter_points SET lat=?, lng=? WHERE id=?", (lat, lng, pid))
    else:
        c.execute("INSERT INTO porter_points(code,name,serves,note,active,lat,lng,sort) "
                  "VALUES(?,?,?,?,?,?,?,100)", (*vals, lat, lng))
    c.commit(); c.close()
    if (f.get("pin") or "").strip() and lat is None:
        flash(f"Saved, but the pin wasn't readable — {how}.", "")
    else:
        flash("Porter point saved.", "ok")
    return redirect(url_for("porter_points"))


@app.route("/porter-points/<int:pid>/delete", methods=["POST"])
def porter_point_delete(pid):
    """Deactivate rather than delete — past sheets refer to these by name."""
    c = conn()
    c.execute("UPDATE porter_points SET active=0 WHERE id=?", (pid,))
    c.commit(); c.close()
    flash("Porter point switched off. Past sheets keep their record of it.", "ok")
    return redirect(url_for("porter_points"))


# ---------------------------------------------------------------------------
# Map pins — the setup step the whole delivery route depends on
# ---------------------------------------------------------------------------
@app.route("/pins")
def pins():
    """Who still needs a map pin. Customers with live subscriptions come first,
    because an unpinned active customer is the thing that breaks tonight's route."""
    c = conn()
    st = ops.pin_status(c)
    klat, klng, confirmed = ops.kitchen(c)
    rows = [(r, ops.maps_search_url(r)) for r in st["missing"]]
    pinned = []
    for r in st["pinned"]:
        km, src = ops.road_km_from_kitchen(c, r["lat"], r["lng"])
        pinned.append((r, round(km, 1), ops.zone_for_km(c, km), src))
    pinned.sort(key=lambda x: x[1])
    prov = ops.provider_status(c)
    c.close()
    return render_template("pins.html", missing=rows, pinned=pinned, st=st,
                           kitchen_confirmed=confirmed, klat=klat, klng=klng,
                           prov=prov)


@app.route("/pins/save", methods=["POST"])
def pins_save():
    """Inline save straight from the pins list — paste, save, next.

    Returns the distance and zone immediately so a pin that landed in the wrong
    place is obvious right there (a Perumbakkam neighbour showing 14 km is a typo,
    not a customer).
    """
    c = conn()
    kind = request.form.get("kind") or "address"
    lat, lng, how = ops.resolve_location(request.form.get("pin"))
    if lat is None:
        c.close()
        return jsonify({"ok": False, "err": how})
    if kind == "trial":
        # A trial has no customer row and no address row — the pin lives on the
        # trial itself, and the route reads it from there.
        c.execute("UPDATE trial_orders SET lat=?, lng=? WHERE id=?",
                  (lat, lng, request.form.get("address_id")))
        c.commit()
    else:
        aid = request.form.get("address_id", type=int)
        c.execute("UPDATE customer_addresses SET lat=?, lng=? WHERE id=?", (lat, lng, aid))
        a = c.execute("SELECT * FROM customer_addresses WHERE id=?", (aid,)).fetchone()
        c.commit()
        if a and a["is_default"]:
            ops.set_default_address(c, a["customer_id"], aid)  # mirror onto the customer row
    km, src = ops.road_km_from_kitchen(c, lat, lng)      # road, not straight-line
    zone = ops.zone_for_km(c, km)
    left = len(ops.pin_status(c)["missing"])
    c.close()
    return jsonify({"ok": True, "lat": round(lat, 6), "lng": round(lng, 6), "how": how,
                    "km": round(km, 1), "km_source": src, "zone": zone, "left": left})


@app.route("/api/customer/<cid>/location", methods=["GET", "POST"])
def api_customer_location(cid):
    """Read or set one customer's map pin, with the distance and zone it implies.

    Used from the subscription form so a location can be attached at the moment
    an order is set up, rather than being discovered missing at 6:40 PM.
    """
    c = conn()
    cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        c.close()
        return jsonify({"ok": False, "err": "no such customer"}), 404

    if request.method == "POST":
        lat, lng, how = ops.resolve_location(request.form.get("pin"))
        if lat is None:
            c.close()
            return jsonify({"ok": False, "err": how})
        c.execute("UPDATE customers SET lat=?, lng=? WHERE id=?", (lat, lng, cid))
        # The route and the pin list read the ADDRESS, so the pin has to land
        # there too or setting it here would look saved and change nothing.
        aid = db.ensure_address_row(c, cid)
        if aid:
            c.execute("UPDATE customer_addresses SET lat=?, lng=? WHERE id=?", (lat, lng, aid))
        c.commit()
        cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()

    klat, klng, kok = ops.kitchen(c)
    out = {"ok": True, "id": cust["id"], "name": cust["name"],
           "address": cust["address"] or "", "locality": cust["locality"] or "",
           "search": ops.maps_search_url(cust), "kitchen_set": kok,
           "pinned": cust["lat"] is not None}
    if cust["lat"] is not None:
        km, src = ops.road_km_from_kitchen(c, cust["lat"], cust["lng"])
        out.update({"lat": round(cust["lat"], 6), "lng": round(cust["lng"], 6),
                    "km": round(km, 1), "km_source": src, "zone": ops.zone_for_km(c, km),
                    "corridor": cust["corridor"] or ops.corridor_for(cust["locality"],
                                                                    cust["address"])})
    c.close()
    return jsonify(out)


@app.route("/pins/kitchen", methods=["POST"])
def pins_kitchen():
    """Set the kitchen pin. Everything measures from here, so it is confirmed explicitly."""
    c = conn()
    lat, lng, how = ops.resolve_location(request.form.get("pin"))
    if lat is None:
        c.close()
        flash(f"Couldn't set the kitchen pin — {how}. "
              f"Paste a plus code like V6R3+XM Chennai, or 12.892438, 80.204187.", "")
        return redirect(url_for("pins"))
    db.set_setting(c, "kitchen_lat", lat)
    db.set_setting(c, "kitchen_lng", lng)
    db.set_setting(c, "kitchen_pin_confirmed", "1")
    if (request.form.get("address") or "").strip():
        db.set_setting(c, "kitchen_address", request.form.get("address").strip())
    c.commit(); c.close()
    flash(f"Kitchen pin saved ({how}). Zones and petrol allowance now measure from it.", "ok")
    return redirect(url_for("pins"))


# ---------------------------------------------------------------------------
# Trials
# ---------------------------------------------------------------------------
@app.route("/trials")
def trials():
    c = conn()
    month = request.args.get("month", "")
    sql = "SELECT * FROM trial_orders"
    args = []
    if month:
        sql += " WHERE month=?"
        args.append(month)
    sql += " ORDER BY date DESC, id DESC"
    rows = c.execute(sql, args).fetchall()
    total = c.execute("SELECT COALESCE(SUM(amount),0) s, COUNT(*) n FROM trial_orders").fetchone()
    converted = c.execute("SELECT COUNT(*) n FROM trial_orders WHERE conversion IS NOT NULL "
                          "AND conversion != ''").fetchone()["n"]
    by_month = core.trials_by_month(c)
    c.close()
    return render_template("trials.html", rows=rows, total=total, converted=converted,
                           by_month=by_month, month=month)


def _save_trial(c, f, tid):
    d = f.get("date") or date.today().strftime("%Y-%m-%d")
    dd = f.get("delivery_date") or None
    plan_id = int(f["plan_id"]) if f.get("plan_id") else None
    # This is INSERT OR REPLACE, so every column has to be carried through or it
    # is erased. The pin is only replaced when a new one was actually typed —
    # otherwise an edit to the amount would quietly throw the location away.
    prev = c.execute("SELECT lat, lng FROM trial_orders WHERE id=?", (tid,)).fetchone()
    lat, lng = (prev["lat"], prev["lng"]) if prev else (None, None)
    if (f.get("pin") or "").strip():
        plat, plng, how = ops.resolve_location(f.get("pin"))
        _flash_pin_result(f.get("pin"), plat, how)
        if plat is not None:
            lat, lng = plat, plng
    c.execute("""INSERT OR REPLACE INTO trial_orders(id,date,name,phone,order_text,amount,
                   payment_status,month,conversion,address,notes,delivery_date,meal_type,
                   delivery_status,plan_id,lat,lng,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (tid, d, f.get("name"), f.get("phone"), f.get("order_text"),
               float(f.get("amount") or 0), f.get("payment_status", "Paid"),
               core.month_label_iso(d), f.get("conversion"), f.get("address"),
               f.get("notes"), dd, f.get("meal_type", "Lunch"),
               f.get("delivery_status", "Pending"), plan_id, lat, lng, db.now_iso()))
    c.commit()
    core.sync_trial_delivery(c, tid)     # add/update/remove its slot in the day's orders


@app.route("/trials/new", methods=["GET", "POST"])
def trial_new():
    c = conn()
    if request.method == "POST":
        tid = next_id(c, "trial_orders", "T")
        _save_trial(c, request.form, tid)
        c.close()
        flash(f"Trial {tid} added.", "ok")
        return redirect(url_for("trials"))
    plans = c.execute("SELECT id,name FROM plans WHERE active=1 ORDER BY id").fetchall()
    c.close()
    return render_template("trial_form.html", trial=None, plans=plans,
                           today=date.today().strftime("%Y-%m-%d"))


@app.route("/trials/<tid>/edit", methods=["GET", "POST"])
def trial_edit(tid):
    c = conn()
    trial = c.execute("SELECT * FROM trial_orders WHERE id=?", (tid,)).fetchone()
    if not trial:
        c.close(); abort(404)
    if request.method == "POST":
        _save_trial(c, request.form, tid)
        c.close()
        flash(f"Trial {tid} updated.", "ok")
        return redirect(url_for("trials"))
    plans = c.execute("SELECT id,name FROM plans WHERE active=1 ORDER BY id").fetchall()
    c.close()
    return render_template("trial_form.html", trial=trial, plans=plans,
                           today=date.today().strftime("%Y-%m-%d"))


@app.route("/trials/<tid>/pay", methods=["POST"])
def trial_pay(tid):
    """One-tap payment toggle (Paid <-> Unpaid) straight from the trials list."""
    c = conn()
    t = c.execute("SELECT payment_status FROM trial_orders WHERE id=?", (tid,)).fetchone()
    if not t:
        c.close(); abort(404)
    new = "Unpaid" if (t["payment_status"] == "Paid") else "Paid"
    c.execute("UPDATE trial_orders SET payment_status=? WHERE id=?", (new, tid))
    c.commit(); c.close()
    if _wants_json():
        return jsonify({"ok": True, "payment_status": new})
    flash(f"Trial {tid} marked {new}.", "ok")
    return redirect(request.referrer or url_for("trials"))


@app.route("/trials/<tid>/deliver", methods=["POST"])
def trial_deliver(tid):
    """One-tap delivery toggle (Delivered <-> Pending); keeps the day's order row in sync."""
    c = conn()
    t = c.execute("SELECT delivery_status FROM trial_orders WHERE id=?", (tid,)).fetchone()
    if not t:
        c.close(); abort(404)
    new = "Pending" if (t["delivery_status"] == "Delivered") else "Delivered"
    c.execute("UPDATE trial_orders SET delivery_status=? WHERE id=?", (new, tid))
    c.commit()
    core.sync_trial_delivery(c, tid)          # mirror onto the deliveries / prep list
    c.close()
    if _wants_json():
        return jsonify({"ok": True, "delivery_status": new})
    flash(f"Trial {tid} marked {new}.", "ok")
    return redirect(request.referrer or url_for("trials"))


# ---------------------------------------------------------------------------
# Revenue / dashboard
# ---------------------------------------------------------------------------
def _valid_iso(s):
    """Return s if it is a YYYY-MM-DD date, else None."""
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except (TypeError, ValueError):
        return None


def _month_bounds(scope):
    """(first_iso, last_iso) for a 'Mon-YYYY' scope, or (None, None) for all time."""
    if not scope or scope == "all":
        return None, None
    first = datetime.strptime(scope, "%b-%Y").date().replace(day=1)
    nxt = first.replace(year=first.year + 1, month=1) if first.month == 12 \
        else first.replace(month=first.month + 1)
    return first.strftime("%Y-%m-%d"), (nxt - timedelta(days=1)).strftime("%Y-%m-%d")


def _merge_period(deliv, dkey, sold):
    """One row per period key combining delivered (earned) + sold (booked) buckets,
    so the breakdown table also shows days/weeks where a pack was sold but nothing
    was delivered. Returns a list sorted oldest→newest."""
    def blank(key):
        return {"key": key, "deliv_value": 0, "deliv_meals": 0, "deliv_food": 0,
                "deliv_delivery": 0, "sold_value": 0, "sold_count": 0,
                "sold_food": 0, "sold_delivery": 0}
    rows = {}
    for b in deliv:
        r = rows.setdefault(b[dkey], blank(b[dkey]))
        r.update(deliv_value=b["overall"], deliv_meals=b["delivered"],
                 deliv_food=b["food"], deliv_delivery=b["delivery"])
    for s in sold:
        r = rows.setdefault(s["key"], blank(s["key"]))
        r.update(sold_value=s["value"], sold_count=s["count"],
                 sold_food=s["food"], sold_delivery=s["delivery"])
    return sorted(rows.values(), key=lambda r: r["key"])


def _revenue_focus(c, period, scope, days, weeks):
    """Build the 'focused period' panel for the revenue date filter: the delivered
    orders + subscriptions sold for ONE specific day (?d=) or week (?w=). When a
    month scope is set, it defaults to the latest active period in that month and
    keeps the date picker / arrows inside the month. Returns None for the month view."""
    if period not in ("day", "week"):
        return None
    today = date.today()
    today_iso = today.strftime("%Y-%m-%d")
    lo, hi = _month_bounds(scope)
    param = "d" if period == "day" else "w"
    raw = _valid_iso(request.args.get(param))
    if not raw:                                   # no explicit pick → sensible default
        if period == "day":
            raw = days[-1]["day"] if days else (lo or today_iso)
        else:
            raw = weeks[-1]["week_start"] if weeks else (lo or today_iso)
    base = datetime.strptime(raw, "%Y-%m-%d").date()
    if period == "week":
        base = base - timedelta(days=base.weekday())          # snap to that week's Monday
    key = base.strftime("%Y-%m-%d")
    step = timedelta(days=7 if period == "week" else 1)
    this_start = today - timedelta(days=today.weekday()) if period == "week" else today
    nxt_iso = (base + step).strftime("%Y-%m-%d")
    prev_iso = (base - step).strftime("%Y-%m-%d")
    focus = {
        "period": period, "param": param, "key": key, "pick": key,
        "prev": prev_iso, "next": nxt_iso,
        "can_next": base < this_start and (not hi or nxt_iso <= hi),
        "can_prev": (not lo or prev_iso >= lo),
        "min": lo, "max": (min(hi, today_iso) if hi else today_iso),
        "delivered": core.delivered_detail(c, period, key),
        "subs": core.subs_sold_detail(c, period, key),
    }
    if period == "day":
        focus["title"] = fmt_date(key, "%A, %d %b %Y")
    else:
        end = base + timedelta(days=6)
        focus["title"] = fmt_date(key, "%d %b") + " – " + end.strftime("%d %b %Y")
    return focus


@app.route("/revenue")
def revenue():
    c = conn()
    period = request.args.get("period", "day")       # day | week | month
    months = core.monthly_breakdown(c)
    month_opts = [m["month"] for m in months]
    scope = request.args.get("month", "all")         # 'all' or a 'Mon-YYYY' label
    if scope not in month_opts:
        scope = "all"

    stats = core.headline_stats(c)
    outstanding = core.outstanding_detail(c)         # itemised 'to collect' drill-down
    monthly_rev = core.monthly_revenue(c)            # ACTUAL revenue (subs + trials) by month
    _, _, earned = core.daily_view(c)                # all-time delivered ESTIMATE (day/week only)
    by_plan = c.execute(
        "SELECT plan_name, COUNT(*) n, COALESCE(SUM(total_amount),0) amt, "
        "       COALESCE(SUM(amount_received),0) recv "
        "FROM subscriptions GROUP BY plan_name ORDER BY amt DESC").fetchall()
    # Collection rate against true revenue (subscriptions + trials).
    collected = (stats["received_all"] / stats["overall"] * 100) if stats["overall"] else 0
    this_month = core.month_label_iso()
    cur = next((m for m in monthly_rev if m["month"] == this_month), None)

    # Month-scoped data for the charts + tables (day & week views honour the scope).
    days, _, earned_scope = core.daily_view(c, limit=999, month=scope)
    weeks = core.weekly_breakdown(c, limit=999, month=scope)
    subs_days = core.subs_sold_breakdown(c, "day", month=scope)
    subs_weeks = core.subs_sold_breakdown(c, "week", month=scope)
    subs_months = core.subs_sold_breakdown(c, "month")
    subs_map = {b["key"]: b for b in subs_days}
    subs_week_map = {b["key"]: b for b in subs_weeks}
    subs_month_map = {b["key"]: b for b in subs_months}

    # Scoped headline: delivered (earned) vs subscriptions sold (booked) for the range.
    summary = {
        "label": "All time" if scope == "all"
                 else datetime.strptime(scope, "%b-%Y").strftime("%B %Y"),
        "scoped": scope != "all",
        "deliv_value": earned_scope["overall"], "deliv_meals": earned_scope["delivered"],
        "sold_value": sum(b["value"] for b in subs_days),
        "sold_count": sum(b["count"] for b in subs_days),
        "received": sum(b["received"] for b in subs_days),
    }
    # Unified day/week breakdown tables (delivered + sold side by side).
    day_table = _merge_period(days, "day", subs_days)
    week_table = _merge_period(weeks, "week_start", subs_weeks)
    focus = _revenue_focus(c, period, scope, days, weeks)
    c.close()
    return render_template("revenue.html", months=months, weeks=weeks, days=days,
                           earned=earned, stats=stats, by_plan=by_plan, period=period,
                           collected=collected, cur=cur, this_month=this_month,
                           subs_days=subs_days, subs_weeks=subs_weeks,
                           subs_months=subs_months, subs_map=subs_map,
                           subs_week_map=subs_week_map, subs_month_map=subs_month_map,
                           focus=focus, scope=scope, month_opts=month_opts, summary=summary,
                           day_table=day_table, week_table=week_table, monthly_rev=monthly_rev,
                           outstanding=outstanding)


# ---------------------------------------------------------------------------
# CRM — leads → conversion
# ---------------------------------------------------------------------------
@app.route("/crm")
def crm_home():
    c = conn()
    stage = request.args.get("stage", "Open")
    q = request.args.get("q", "").strip()
    leads = crm.list_leads(c, stage=stage, q=q)
    due = crm.followups_due(c)
    stats = crm.pipeline_stats(c)
    c.close()
    return render_template("crm.html", leads=leads, due=due, stats=stats,
                           stage=stage, q=q, stages=crm.STAGES,
                           open_stages=crm.OPEN_STAGES)


@app.route("/crm/new", methods=["GET", "POST"])
def crm_new():
    c = conn()
    if request.method == "POST":
        lid = crm.save_lead(c, request.form)
        c.close()
        flash(f"Lead {lid} added.", "ok")
        return redirect(url_for("crm_detail", lid=lid))
    c.close()
    return render_template("crm_form.html", lead=None, sources=crm.SOURCES,
                           goals=crm.GOALS, stages=crm.STAGES,
                           today=date.today().strftime("%Y-%m-%d"))


@app.route("/crm/import-trials", methods=["POST"])
def crm_import_trials():
    c = conn()
    n = crm.import_from_trials(c)
    c.close()
    flash(f"Imported {n} lead{'s' if n != 1 else ''} from trial orders." if n
          else "No new trials to import — all caught up.", "ok")
    return redirect(url_for("crm_home"))


@app.route("/crm/<lid>")
def crm_detail(lid):
    c = conn()
    lead = crm.get_lead(c, lid)
    if not lead:
        c.close(); abort(404)
    lead = dict(lead)
    updates = crm.updates_for(c, lid)
    cust = c.execute("SELECT * FROM customers WHERE id=?", (lead["customer_id"],)).fetchone() \
        if lead["customer_id"] else None
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (lead["subscription_id"],)).fetchone() \
        if lead["subscription_id"] else None
    trial = c.execute("SELECT * FROM trial_orders WHERE id=?", (lead["trial_id"],)).fetchone() \
        if lead["trial_id"] else None
    c.close()
    # One-tap "send the brochure" on WhatsApp, personalised to this lead.
    wa_brochure = None
    if lead["phone"]:
        bro_url = request.url_root.rstrip("/") + url_for("brochure_public")
        txt = (f"Hi {(lead['name'] or '').split(' ')[0]}! 🌱 Here's the OG Gainz brochure — "
               f"plans, menus & pricing:\n{bro_url}\n\nStart with a trial, no commitment! 💚")
        wa_brochure = wa_link(lead["phone"]) + "?text=" + urllib_quote(txt)
    return render_template("crm_detail.html", lead=lead, updates=updates, cust=cust,
                           sub=sub, trial=trial, stages=crm.STAGES,
                           open_stages=crm.OPEN_STAGES, wa_brochure=wa_brochure)


@app.route("/crm/<lid>/edit", methods=["GET", "POST"])
def crm_edit(lid):
    c = conn()
    lead = crm.get_lead(c, lid)
    if not lead:
        c.close(); abort(404)
    if request.method == "POST":
        crm.save_lead(c, request.form, lid=lid)
        c.close()
        flash(f"Lead {lid} updated.", "ok")
        return redirect(url_for("crm_detail", lid=lid))
    c.close()
    return render_template("crm_form.html", lead=lead, sources=crm.SOURCES,
                           goals=crm.GOALS, stages=crm.STAGES,
                           today=date.today().strftime("%Y-%m-%d"))


@app.route("/crm/<lid>/stage", methods=["POST"])
def crm_stage(lid):
    c = conn()
    stage = request.form.get("stage")
    reason = request.form.get("lost_reason")
    crm.set_stage(c, lid, stage, reason)
    c.close()
    if _wants_json():
        return jsonify({"ok": True, "stage": stage})
    flash(f"Moved to {stage}.", "ok")
    return redirect(request.referrer or url_for("crm_detail", lid=lid))


@app.route("/crm/<lid>/update", methods=["POST"])
def crm_update(lid):
    c = conn()
    kind = request.form.get("kind", "Note")
    text = (request.form.get("text") or "").strip()
    follow = request.form.get("next_follow_up")
    if text:
        crm.log_update(c, lid, kind, text)
    if follow:
        crm.set_follow_up(c, lid, follow)
    c.close()
    flash("Update saved." if (text or follow) else "Nothing to save.", "ok")
    return redirect(url_for("crm_detail", lid=lid))


@app.route("/crm/<lid>/followup", methods=["POST"])
def crm_followup(lid):
    c = conn()
    crm.set_follow_up(c, lid, request.form.get("next_follow_up") or None)
    c.close()
    flash("Follow-up updated.", "ok")
    return redirect(request.referrer or url_for("crm_detail", lid=lid))


@app.route("/crm/<lid>/convert", methods=["POST"])
def crm_convert(lid):
    c = conn()
    lead = crm.get_lead(c, lid)
    if not lead:
        c.close(); abort(404)
    cid = crm.convert_lead(c, lid)
    c.close()
    flash(f"Converted to customer {cid}. Now book their pack 👇", "ok")
    return redirect(url_for("subscription_new", customer=cid, lead=lid))


# ---------------------------------------------------------------------------
# Settings / plans / data tools
# ---------------------------------------------------------------------------
WEEKDAYS = [("0", "Mon"), ("1", "Tue"), ("2", "Wed"), ("3", "Thu"),
            ("4", "Fri"), ("5", "Sat"), ("6", "Sun")]


@app.route("/settings/distance", methods=["POST"])
def settings_distance():
    """Choose how road distance is measured. This decides whether the petrol
    allowance is a real figure or an approximation, so it gets its own screen."""
    c = conn()
    f = request.form
    db.set_setting(c, "distance_provider", (f.get("distance_provider") or "").strip())
    pref = (f.get("ors_preference") or "").strip()
    if pref in ("shortest", "recommended", "fastest"):
        db.set_setting(c, "ors_preference", pref)
    for k in ("routes_api_key", "ors_api_key", "osrm_base_url"):
        v = (f.get(k) or "").strip()
        if v != "********":                     # unchanged masked value
            db.set_setting(c, k, v)
    for k in ("petrol_rate", "zone_hyperlocal_km", "zone_near_km", "zone_mid_km"):
        if (f.get(k) or "").strip():
            db.set_setting(c, k, f.get(k).strip())
    # An unticked checkbox posts nothing, so this is written every save either way.
    db.set_setting(c, "porter_auto", "1" if f.get("porter_auto") else "0")
    c.commit()
    if f.get("test"):
        res = ops.check_provider(c)
        # Any cached estimates are now stale — drop them so real distances are fetched.
        if res["ok"]:
            c.execute("DELETE FROM route_km_cache WHERE source='estimate'")
            c.commit()
        flash(res["msg"], "ok" if res["ok"] else "")
    else:
        flash("Distance settings saved.", "ok")
    c.close()
    return redirect(url_for("settings") + "#distance")


@app.route("/settings/distance/clear-cache", methods=["POST"])
def settings_distance_clear():
    """Forget every cached distance and re-measure from scratch."""
    c = conn()
    n = c.execute("SELECT COUNT(*) n FROM route_km_cache").fetchone()["n"]
    c.execute("DELETE FROM route_km_cache")
    c.commit(); c.close()
    flash(f"Cleared {n} cached distance(s). They will be re-measured as needed.", "ok")
    return redirect(url_for("settings") + "#distance")


@app.route("/settings")
def settings():
    c = conn()
    plans = c.execute("SELECT * FROM plans ORDER BY active DESC, name").fetchall()
    overrides = c.execute("SELECT * FROM day_overrides ORDER BY date DESC").fetchall()
    threshold = core.ending_soon_threshold(c)
    op_days = core.operating_days(c)
    backups = []
    bdir = os.path.join(APP_DIR, "backups")
    if os.path.isdir(bdir):
        backups = sorted(os.listdir(bdir), reverse=True)[:10]
    dist = {
        "prov": ops.provider_status(c),
        "provider": ops.setting(c, "distance_provider"),
        "has_google": bool(ops.setting(c, "routes_api_key")),
        "has_ors": bool(ops.setting(c, "ors_api_key")),
        "ors_pref": ops.setting(c, "ors_preference"),
        "osrm_url": ops.setting(c, "osrm_base_url"),
        "petrol_rate": ops.setting(c, "petrol_rate"),
        "porter_auto": str(ops.setting(c, "porter_auto") or "0") == "1",
        "porter_min": ops.setting(c, "porter_min_km"),
        "z1": ops.setting(c, "zone_hyperlocal_km"),
        "z2": ops.setting(c, "zone_near_km"),
        "z3": ops.setting(c, "zone_mid_km"),
        "cached": c.execute("SELECT COUNT(*) n FROM route_km_cache").fetchone()["n"],
    }
    c.close()
    return render_template("settings.html", plans=plans, overrides=overrides, dist=dist,
                           threshold=threshold, backups=backups,
                           weekdays=WEEKDAYS, op_days=op_days,
                           today=date.today().strftime("%Y-%m-%d"))


@app.route("/settings/weekdays", methods=["POST"])
def settings_weekdays():
    c = conn()
    chosen = request.form.getlist("weekday")           # list of '0'..'6'
    if not chosen:
        chosen = ["0", "1", "2", "3", "4"]
    db.set_setting(c, "operating_weekdays", ",".join(sorted(chosen)))
    c.close()
    flash("Working days updated.", "ok")
    return redirect(url_for("settings"))


@app.route("/settings/threshold", methods=["POST"])
def settings_threshold():
    c = conn()
    db.set_setting(c, "ending_soon_threshold", request.form.get("threshold", "2"))
    core.recompute_all(c)
    c.close()
    flash("Ending-soon threshold updated.", "ok")
    return redirect(url_for("settings"))


@app.route("/settings/plan", methods=["POST"])
def settings_plan():
    c = conn()
    f = request.form
    pid = f.get("id")
    vals = (f.get("name"), f.get("category"),
            float(f["price_monthly"]) if f.get("price_monthly") else None,
            float(f["price_weekly"]) if f.get("price_weekly") else None,
            float(f["price_trial"]) if f.get("price_trial") else None,
            int(f["validity_days"]) if f.get("validity_days") else None)
    if pid:
        c.execute("UPDATE plans SET name=?,category=?,price_monthly=?,price_weekly=?,"
                  "price_trial=?,validity_days=? WHERE id=?", vals + (pid,))
    else:
        c.execute("INSERT INTO plans(name,category,price_monthly,price_weekly,price_trial,"
                  "validity_days,servings_monthly,servings_weekly,meals_per_day,default_slot,active) "
                  "VALUES(?,?,?,?,?,?,20,5,1,'Lunch',1)", vals)
    c.commit(); c.close()
    flash("Plan saved.", "ok")
    return redirect(url_for("settings"))


@app.route("/export")
def export():
    import exporter
    path = exporter.export_workbook()
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/backup", methods=["POST"])
def backup_now():
    import backup
    path = backup.make_backup()
    flash(f"Backup saved: {os.path.basename(path)}", "ok")
    return redirect(url_for("settings"))


@app.route("/reimport", methods=["POST"])
def reimport():
    # Re-import wipes the DB and rebuilds it from the laptop's NEW TRACKER.xlsx. That
    # spreadsheet does not exist on the online server, and the live app is now the source
    # of truth — so this is laptop-only. Refuse on the deployed app no matter what.
    if APP_PASSWORD:
        flash("Re-import only runs on your laptop (where the Excel file lives). "
              "Your online data is the live copy and was not touched.", "ok")
        return redirect(url_for("settings"))
    import importer, backup
    backup.make_backup()                 # safety net before overwrite
    importer.run_import()
    flash("Re-imported from NEW TRACKER.xlsx (a backup was taken first).", "ok")
    return redirect(url_for("today"))


# ---------------------------------------------------------------------------
# Delivery schedule: which weekdays, which slot, which box
# ---------------------------------------------------------------------------
@app.route("/subscriptions/<sid>/schedule", methods=["POST"])
def subscription_schedule(sid):
    c = conn()
    if not c.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        c.close(); abort(404)
    _save_schedule_from_form(c, sid, request.form)
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    summary = core.schedule_summary(c, sub)
    c.close()
    flash(f"Schedule saved — {summary}.", "ok")
    return redirect(url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/variants", methods=["POST"])
def subscription_variants(sid):
    """Add or remove one of a subscription's alternative boxes."""
    c = conn()
    if not c.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        c.close(); abort(404)
    f = request.form
    if f.get("do") == "delete":
        menu.delete_variant(c, sid, int(f["vid"]))
        flash("Alternative box removed — those days go back to the usual meal.", "ok")
    else:
        name = (f.get("name") or "").strip()
        plan_id = int(f["plan_id"]) if f.get("plan_id") else None
        if name or plan_id:
            if not name and plan_id:
                p = c.execute("SELECT name FROM plans WHERE id=?", (plan_id,)).fetchone()
                name = p["name"] if p else "Alternative box"
            vid = menu.add_variant(c, sid, name, plan_id)
            flash(f"“{name}” added — now pick the days it goes out on.", "ok")
        else:
            flash("Give the alternative box a name (or pick a pack to copy).", "ok")
    c.close()
    return redirect(url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/variant/<int:vid>", methods=["GET", "POST"])
def subscription_variant_edit(sid, vid):
    """Edit what goes in one alternative box — the same builder as the main pack."""
    c = conn()
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    var = menu.variant(c, vid)
    if not sub or not var or var["subscription_id"] != sid:
        c.close(); abort(404)
    if request.method == "POST":
        raw = request.form.get("comp_json")
        items = json.loads(raw) if raw else []
        base = [(int(i["id"]), float(i["qty"]), (i.get("note") or "").strip())
                for i in items if i.get("kind") != "addon" and float(i.get("qty") or 0) > 0]
        addons = [(int(i["id"]), float(i["qty"]), (i.get("note") or "").strip())
                  for i in items if i.get("kind") == "addon" and float(i.get("qty") or 0) > 0]
        menu.save_sub_composition(c, sid, base, addons, variant_id=vid)
        if request.form.get("name"):
            c.execute("UPDATE subscription_variants SET name=? WHERE id=?",
                      (request.form["name"].strip(), vid))
            c.commit()
        c.close()
        flash(f"“{var['name']}” updated.", "ok")
        return redirect(url_for("subscription_detail", sid=sid))
    existing = [{"id": r["component_id"], "qty": r["qty"], "kind": r["kind"],
                 "note": r["note"] or ""} for r in menu.sub_composition(c, sid, vid)]
    menu_data = _menu_data(c)
    c.close()
    return render_template("variant_form.html", sub=sub, variant=var,
                           existing_comp=existing, menu_data=menu_data)


# ---------------------------------------------------------------------------
# Extra meals — a second box drawn from the pack the customer already bought
# ---------------------------------------------------------------------------
@app.route("/subscriptions/<sid>/extra", methods=["POST"])
def subscription_extra(sid):
    c = conn()
    if not c.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        c.close(); abort(404)
    f = request.form
    day = (f.get("date") or "").strip() or date.today().strftime("%Y-%m-%d")
    did = core.add_extra_meal(
        c, sid, day, slot=f.get("slot") or None,
        counts_against_pack=(f.get("counts_against_pack", "1") == "1"),
        note=f.get("note") or None,
        variant_id=int(f["variant_id"]) if f.get("variant_id") else None,
        status=f.get("status") or "Pending")
    sub = c.execute("SELECT delivered_count,total_meals_scheduled,remaining_meals "
                    "FROM subscriptions WHERE id=?", (sid,)).fetchone()
    c.close()
    if did:
        flash(f"Extra meal added for {day[8:10]}/{day[5:7]} — now "
              f"{sub['delivered_count']}/{sub['total_meals_scheduled']} delivered, "
              f"{sub['remaining_meals']} left.", "ok")
    return redirect(request.form.get("back") or url_for("subscription_detail", sid=sid))


@app.route("/api/delivery/<int:did>/delete", methods=["POST"])
def api_delivery_delete(did):
    """Remove an order row — used to undo an extra meal added by mistake."""
    c = conn()
    row = c.execute("SELECT is_extra FROM deliveries WHERE id=?", (did,)).fetchone()
    if not row:
        c.close()
        return jsonify({"ok": False, "error": "not found"}), 404
    # Only ever a hand-added extra: the scheduled orders are managed by the app and
    # deleting one here would just make it reappear on the next page load.
    if not (row["is_extra"] or 0):
        c.close()
        return jsonify({"ok": False, "error": "only extra meals can be removed"}), 400
    core.delete_delivery(c, did)
    c.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Week planner — the customers who confirm their days one at a time
# ---------------------------------------------------------------------------
@app.route("/planner", methods=["GET", "POST"])
def planner():
    """Tick next week's days for every on-demand customer in one screen.

    These are the subscribers who take one to three meals a week and tell us the
    day before. Nothing is generated for them automatically, so this is where
    their week gets set — all of them together, rather than opening each
    subscription in turn.
    """
    c = conn()
    start = (request.values.get("start") or "").strip()
    try:
        monday = datetime.strptime(start, "%Y-%m-%d").date()
    except ValueError:
        today = date.today()
        # Default to the week that is being planned: this week until Friday,
        # next week once the working week is basically over.
        monday = today - timedelta(days=today.weekday())
        if today.weekday() >= 4:
            monday += timedelta(days=7)
    week = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    open_days = [d for d in week if core.is_operating_day(c, d)]

    if request.method == "POST":
        sid = request.form.get("sid")
        if sid:
            added, removed = core.plan_week(
                c, sid, request.form.getlist("day"), open_days)
            flash(f"{sid}: {added} meal(s) planned, {removed} removed.", "ok")
        c.close()
        return redirect(url_for("planner", start=monday.strftime("%Y-%m-%d")))

    subs = c.execute(
        "SELECT * FROM subscriptions WHERE COALESCE(schedule_mode,'daily')='ondemand' "
        "AND status IN ('Active','Ending Soon','Paused') ORDER BY customer_name").fetchall()
    rows = []
    for s in subs:
        planned = {r["date"]: r for r in c.execute(
            "SELECT * FROM deliveries WHERE subscription_id=? AND date BETWEEN ? AND ?",
            (s["id"], week[0], week[-1])).fetchall()}
        rows.append({"sub": s, "planned": planned,
                     "frozen": {d: core.is_paused_on(c, s["id"], d) for d in open_days}})
    c.close()
    return render_template("planner.html", rows=rows, week=week, open_days=open_days,
                           monday=monday.strftime("%Y-%m-%d"),
                           prev_week=(monday - timedelta(days=7)).strftime("%Y-%m-%d"),
                           next_week=(monday + timedelta(days=7)).strftime("%Y-%m-%d"),
                           today=date.today().strftime("%Y-%m-%d"))


# ---------------------------------------------------------------------------
# Composition rules + OG Agent suggestions (per subscription)
# ---------------------------------------------------------------------------
@app.route("/subscriptions/<sid>/rules", methods=["POST"])
def subscription_rules(sid):
    c = conn()
    if not c.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        c.close(); abort(404)
    f = request.form
    action = f.get("do", "add")
    if action == "delete":
        c.execute("DELETE FROM component_rules WHERE id=? AND subscription_id=?",
                  (int(f["rid"]), sid))
        c.commit()
        flash("Rule removed.", "ok")
    else:
        weekdays = ",".join(f.getlist("weekday")) or None
        rule_action = f.get("action", "swap")
        from_id = int(f["from_component"]) if f.get("from_component") else None
        to_id = int(f["to_component"]) if f.get("to_component") else None
        qty = float(f["qty"]) if f.get("qty") else None
        ok = ((rule_action == "swap" and from_id and to_id) or
              (rule_action == "add" and to_id) or
              (rule_action == "remove" and from_id))
        if ok:
            c.execute("INSERT INTO component_rules(subscription_id,weekdays,action,"
                      "from_component_id,to_component_id,qty,note,source,active,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,1,?)",
                      (sid, weekdays, rule_action, from_id, to_id, qty,
                       f.get("note") or None, "manual", db.now_iso()))
            c.commit()
            flash("Rule saved — the prep list applies it automatically.", "ok")
        else:
            flash("Pick the item(s) for the rule first.", "ok")
    c.close()
    return redirect(url_for("subscription_detail", sid=sid))


@app.route("/subscriptions/<sid>/suggest", methods=["POST"])
def subscription_suggest(sid):
    """Apply or dismiss one OG Agent note-parsed suggestion (one tap)."""
    c = conn()
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not sub:
        c.close(); abort(404)
    label = request.form.get("label", "")
    if request.form.get("do") == "apply":
        for p in agent.suggestions_for_sub(c, sub):
            if p["label"] == label:
                c.execute("INSERT INTO component_rules(subscription_id,weekdays,action,"
                          "from_component_id,to_component_id,qty,note,source,active,created_at) "
                          "VALUES(?,?,?,?,?,?,?,?,1,?)",
                          (sid, p["weekdays"], p["action"], p["from_id"], p["to_id"],
                           p["qty"], "from notes (OG Agent)", "agent", db.now_iso()))
                c.commit()
                flash("Applied ✓ — prep list updated.", "ok")
                break
    else:
        agent.dismiss_suggestion(c, sid, label)
        flash("Suggestion dismissed.", "ok")
    c.close()
    return redirect(url_for("subscription_detail", sid=sid))


# ---------------------------------------------------------------------------
# Menu editor (components + pack recipes)
# ---------------------------------------------------------------------------
@app.route("/menu")
def menu_editor():
    c = conn()
    comps = menu.all_components(c, active_only=False)
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY id").fetchall()
    recipes = {p["id"]: menu.plan_recipe(c, p["id"]) for p in plans}
    c.close()
    return render_template("menu.html", comps=comps, plans=plans, recipes=recipes,
                           fmt_qty=menu.fmt_qty)


@app.route("/menu/component", methods=["POST"])
def menu_component():
    c = conn()
    f = request.form
    do = f.get("do", "save")
    cid = f.get("id")
    if do == "toggle" and cid:
        c.execute("UPDATE components SET active=1-active WHERE id=?", (cid,))
        c.commit(); c.close()
        flash("Component updated.", "ok")
        return redirect(url_for("menu_editor"))
    if do == "delete" and cid:
        used = (c.execute("SELECT 1 FROM subscription_components WHERE component_id=? LIMIT 1",
                          (cid,)).fetchone()
                or c.execute("SELECT 1 FROM plan_components WHERE component_id=? LIMIT 1",
                             (cid,)).fetchone())
        if used:
            c.execute("UPDATE components SET active=0 WHERE id=?", (cid,))
            flash("That item is used in packs/subscriptions, so it was hidden instead of deleted.", "ok")
        else:
            c.execute("DELETE FROM components WHERE id=?", (cid,))
            flash("Component deleted.", "ok")
        c.commit(); c.close()
        return redirect(url_for("menu_editor"))
    vals = (f.get("name", "").strip(), f.get("unit", "serving"),
            float(f.get("step") or 1), float(f.get("default_qty") or 1),
            float(f["addon_price"]) if f.get("addon_price") else None,
            f.get("protein") or None, f.get("emoji") or None,
            int(f.get("sort") or 100))
    if not vals[0]:
        c.close()
        flash("Give the item a name.", "ok")
        return redirect(url_for("menu_editor"))
    if cid:
        c.execute("UPDATE components SET name=?,unit=?,step=?,default_qty=?,addon_price=?,"
                  "protein=?,emoji=?,sort=? WHERE id=?", vals + (cid,))
    else:
        c.execute("INSERT INTO components(name,unit,step,default_qty,addon_price,protein,"
                  "emoji,sort,active) VALUES(?,?,?,?,?,?,?,?,1)", vals)
    c.commit(); c.close()
    flash("Menu item saved.", "ok")
    return redirect(url_for("menu_editor"))


@app.route("/menu/recipe/<int:pid>", methods=["POST"])
def menu_recipe(pid):
    """Replace one pack's recipe from rows of comp_<id>=qty inputs."""
    c = conn()
    c.execute("DELETE FROM plan_components WHERE plan_id=?", (pid,))
    for key, val in request.form.items():
        if key.startswith("comp_") and val not in ("", "0"):
            try:
                cid, qty = int(key[5:]), float(val)
            except ValueError:
                continue
            if qty > 0:
                note = (request.form.get(f"note_{cid}") or "").strip() or None
                c.execute("INSERT INTO plan_components(plan_id,component_id,qty,note) "
                          "VALUES(?,?,?,?)", (pid, cid, qty, note))
    c.commit(); c.close()
    flash("Pack recipe updated. New subscriptions pick this up automatically.", "ok")
    return redirect(url_for("menu_editor"))


# ---------------------------------------------------------------------------
# Invoices + Terms
# ---------------------------------------------------------------------------
def _invoice_items_for_sub(c, sub):
    """Auto-build invoice line items from a subscription (pack + add-ons + delivery)."""
    meals = sub["total_meals_scheduled"] or 0
    addons = [r for r in menu.sub_composition(c, sub["id"]) if r["kind"] == "addon"]
    addon_total = 0
    items = []
    for a in addons:
        per_meal = (a["addon_price"] or 0) * (a["qty"] / (a["step"] or 1))
        amt = round(per_meal * meals)
        addon_total += amt
        items.append({"desc": f"Add-on · {a['name']} {menu.fmt_qty(a['qty'], a['unit'])}/meal",
                      "qty": meals, "rate": round(per_meal), "amount": amt})
    pack_amt = max((sub["subscription_price"] or 0) - addon_total, 0)
    # Keep the line item lean — servings/slot/timing live in the invoice's
    # subscription-details panel, not repeated here.
    label = f"{sub['plan_name']} · {sub['duration'] or 'Monthly'} subscription"
    items.insert(0, {"desc": label, "qty": 1, "rate": pack_amt, "amount": pack_amt})
    if sub["delivery_fee"]:
        items.append({"desc": "Delivery fee", "qty": 1, "rate": sub["delivery_fee"],
                      "amount": sub["delivery_fee"]})
    return items


@app.route("/invoices")
def invoices():
    c = conn()
    rows = c.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()
    subs = c.execute("SELECT id, customer_name, plan_name FROM subscriptions "
                     "ORDER BY id DESC").fetchall()
    c.close()
    return render_template("invoices.html", rows=rows, subs=subs)


@app.route("/invoices/new", methods=["GET", "POST"])
def invoice_new():
    c = conn()
    if request.method == "GET" and request.args.get("sub"):
        # One-tap: auto-generate from a subscription, then open it for review.
        sub = c.execute("SELECT * FROM subscriptions WHERE id=?",
                        (request.args["sub"],)).fetchone()
        if not sub:
            c.close(); abort(404)
        cust = c.execute("SELECT * FROM customers WHERE id=?", (sub["customer_id"],)).fetchone()
        iid = next_id(c, "invoices", "INV-")
        items = _invoice_items_for_sub(c, sub)
        total = sum(i["amount"] for i in items)
        c.execute("INSERT INTO invoices(id,token,customer_id,subscription_id,bill_name,"
                  "bill_phone,bill_address,inv_date,items,discount,total,received,"
                  "payment_status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (iid, secrets.token_urlsafe(16), sub["customer_id"], sub["id"],
                   sub["customer_name"], cust["phone"] if cust else None,
                   cust["address"] if cust else None,
                   date.today().strftime("%Y-%m-%d"), json.dumps(items), 0, total,
                   sub["amount_received"] or 0, sub["payment_status"] or "Unpaid",
                   None, db.now_iso(), db.now_iso()))
        c.commit(); c.close()
        flash(f"Invoice {iid} generated — review, tweak if needed, then send.", "ok")
        return redirect(url_for("invoice_edit", iid=iid))
    c.close()
    return redirect(url_for("invoices"))


def _save_invoice_form(c, iid, f):
    try:
        items = json.loads(f.get("items_json") or "[]")
    except ValueError:
        items = []
    items = [{"desc": (i.get("desc") or "").strip(),
              "qty": float(i.get("qty") or 1),
              "rate": float(i.get("rate") or 0),
              "amount": round(float(i.get("qty") or 1) * float(i.get("rate") or 0))}
             for i in items if (i.get("desc") or "").strip()]
    discount = float(f.get("discount") or 0)
    total = max(sum(i["amount"] for i in items) - discount, 0)
    received = float(f.get("received") or 0)
    ps = "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid")
    c.execute("UPDATE invoices SET bill_name=?, bill_phone=?, bill_address=?, inv_date=?, "
              "items=?, discount=?, total=?, received=?, payment_status=?, notes=?, updated_at=? "
              "WHERE id=?",
              (f.get("bill_name"), f.get("bill_phone"), f.get("bill_address"),
               f.get("inv_date") or date.today().strftime("%Y-%m-%d"),
               json.dumps(items), discount, total, received, ps,
               (f.get("notes") or "").strip() or None, db.now_iso(), iid))
    c.commit()


@app.route("/invoices/<iid>/edit", methods=["GET", "POST"])
def invoice_edit(iid):
    c = conn()
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
    if not inv:
        c.close(); abort(404)
    if request.method == "POST":
        _save_invoice_form(c, iid, request.form)
        c.close()
        flash("Invoice saved.", "ok")
        return redirect(url_for("invoice_edit", iid=iid))
    items = json.loads(inv["items"] or "[]")
    public_url, wa_url = _invoice_share(inv)
    c.close()
    return render_template("invoice_form.html", inv=inv, items=items,
                           public_url=public_url, wa_url=wa_url)


def _invoice_share(inv):
    public_url = request.url_root.rstrip("/") + url_for("invoice_public", token=inv["token"])
    wa_text = (f"Hi {inv['bill_name']}! 🌱 Here's your OG Gainz invoice {inv['id']} "
               f"for {inr(inv['total'])}.\n\nView / download it here:\n{public_url}\n\n"
               "Our Terms & Conditions are attached on page 2 of the invoice. "
               "Thank you for eating like it matters! 💚")
    wa_url = (wa_link(inv["bill_phone"]) + "?text=" + urllib_quote(wa_text)) \
        if inv["bill_phone"] else None
    return public_url, wa_url


def _invoice_subinfo(c, inv):
    """The subscription behind an invoice — feeds the invoice's details panel
    (plan, servings, slot, preferred timing, start date, validity)."""
    if not inv["subscription_id"]:
        return None
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?",
                    (inv["subscription_id"],)).fetchone()
    if not sub:
        return None
    plan = menu.match_plan(c, sub["plan_name"])
    return {"sub": sub, "validity": plan["validity_days"] if plan else None}


def urllib_quote(s):
    from urllib.parse import quote
    return quote(s)


@app.route("/invoices/<iid>/delete", methods=["POST"])
def invoice_delete(iid):
    c = conn()
    c.execute("DELETE FROM invoices WHERE id=?", (iid,))
    c.commit(); c.close()
    flash(f"Invoice {iid} deleted.", "ok")
    return redirect(url_for("invoices"))


@app.route("/i/<token>")
def invoice_public(token):
    """Customer-facing invoice — no login, protected by the unguessable token."""
    c = conn()
    inv = c.execute("SELECT * FROM invoices WHERE token=?", (token,)).fetchone()
    if not inv:
        c.close(); abort(404)
    items = json.loads(inv["items"] or "[]")
    subinfo = _invoice_subinfo(c, inv)
    c.close()
    return render_template("invoice_view.html", inv=inv, items=items, internal=False,
                           subinfo=subinfo)


@app.route("/invoices/<iid>")
def invoice_view(iid):
    c = conn()
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
    if not inv:
        c.close(); abort(404)
    items = json.loads(inv["items"] or "[]")
    public_url, wa_url = _invoice_share(inv)
    subinfo = _invoice_subinfo(c, inv)
    c.close()
    return render_template("invoice_view.html", inv=inv, items=items, internal=True,
                           public_url=public_url, wa_url=wa_url, subinfo=subinfo)


@app.route("/terms")
def terms():
    return render_template("terms.html")


# ---------------------------------------------------------------------------
# Digital brochure — public page, editable any time, sendable to anyone
# ---------------------------------------------------------------------------
def _brochure_share():
    """(public_url, wa_pick_url): the brochure's public link + a WhatsApp share
    that lets Balaji pick any contact (or paste into a group)."""
    public_url = request.url_root.rstrip("/") + url_for("brochure_public")
    wa_text = ("🌱 *OG GAINZ — Eat like it matters.*\n"
               "Macro-tracked, freshly cooked meal subscriptions in Chennai.\n\n"
               f"Full brochure — plans, menus & pricing:\n{public_url}\n\n"
               "Start with a trial! 💚")
    return public_url, "https://wa.me/?text=" + urllib_quote(wa_text)


@app.route("/brochure")
def brochure_public():
    """The customer-facing brochure. Public (shareable link, no login); when
    Balaji himself is viewing, the edit/share toolbar appears on top."""
    c = conn()
    secs = [(s, brochure.render_body(s["body"])) for s in brochure.sections(c)]
    c.close()
    internal = (not APP_PASSWORD) or bool(session.get("authed"))
    public_url, wa_url = _brochure_share()
    return render_template("brochure_view.html", secs=secs, internal=internal,
                           public_url=public_url, wa_url=wa_url)


@app.route("/brochure/edit")
def brochure_edit():
    c = conn()
    secs = brochure.sections(c, active_only=False)
    c.close()
    return render_template("brochure_edit.html", secs=secs)


@app.route("/brochure/section", methods=["POST"])
def brochure_section():
    """Add / save / toggle / delete one brochure section."""
    c = conn()
    f = request.form
    do = f.get("do", "save")
    sid = f.get("id")
    if do == "toggle" and sid:
        c.execute("UPDATE brochure_sections SET active=1-active, updated_at=? WHERE id=?",
                  (db.now_iso(), sid))
        flash("Section visibility updated.", "ok")
    elif do == "delete" and sid:
        c.execute("DELETE FROM brochure_sections WHERE id=?", (sid,))
        flash("Section deleted.", "ok")
    else:
        title = (f.get("title") or "").strip()
        if not title:
            c.close()
            flash("Give the section a title.", "ok")
            return redirect(url_for("brochure_edit"))
        vals = (int(f.get("sort") or 100), title, (f.get("subtitle") or "").strip() or None,
                f.get("body") or "", db.now_iso())
        if sid:
            c.execute("UPDATE brochure_sections SET sort=?, title=?, subtitle=?, body=?, "
                      "updated_at=? WHERE id=?", vals + (sid,))
        else:
            c.execute("INSERT INTO brochure_sections(sort,title,subtitle,body,updated_at,active) "
                      "VALUES(?,?,?,?,?,1)", vals)
        flash("Brochure updated — the public link shows it immediately.", "ok")
    c.commit(); c.close()
    return redirect(url_for("brochure_edit"))


# ---------------------------------------------------------------------------
# OG Agent — chat, notifications, settings
# ---------------------------------------------------------------------------
@app.route("/agent")
def agent_home():
    c = conn()
    msgs = c.execute("SELECT * FROM agent_messages ORDER BY id DESC LIMIT 40").fetchall()
    ready = agent.ai_ready(c)
    latest = c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 5").fetchall()
    learned = agent.facts(c)
    c.close()
    return render_template("agent.html", msgs=list(reversed(msgs)), ready=ready,
                           latest=latest, facts=learned)


@app.route("/agent/history")
def agent_history():
    """Recent chat as JSON — feeds the floating OG Agent chat on every page."""
    c = conn()
    msgs = c.execute("SELECT role, content FROM agent_messages "
                     "ORDER BY id DESC LIMIT 30").fetchall()
    ready = agent.ai_ready(c)
    c.close()
    return jsonify({"ok": True, "ready": ready,
                    "msgs": [{"role": m["role"], "content": m["content"]}
                             for m in reversed(msgs)]})


@app.route("/agent/fact/<int:fid>/delete", methods=["POST"])
def agent_fact_delete(fid):
    c = conn()
    agent.delete_fact(c, fid)
    c.close()
    flash("Forgotten — that fact was removed from OG Agent's memory.", "ok")
    return redirect(request.referrer or url_for("agent_home"))


@app.route("/agent/fact", methods=["POST"])
def agent_fact_add():
    """Teach OG Agent a fact directly (no chat needed)."""
    c = conn()
    saved = agent.add_fact(c, request.form.get("fact"), request.form.get("topic"),
                           source="manual")
    c.close()
    flash("OG Agent learned it ✓" if saved else "Type the fact first.", "ok")
    return redirect(request.referrer or url_for("agent_home"))


@app.route("/agent/chat", methods=["POST"])
def agent_chat():
    text = (request.form.get("q") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Say something first."})
    c = conn()
    reply, actions, err = agent.ai_chat(c, text)
    c.close()
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True, "reply": reply, "actions": actions})


@app.route("/agent/action", methods=["POST"])
def agent_action():
    """Apply ONE confirmed agent-proposed action (rule / reminder)."""
    try:
        a = json.loads(request.form.get("action") or "{}")
    except ValueError:
        a = {}
    c = conn()
    result = agent.apply_action(c, a)
    c.close()
    return jsonify({"ok": bool(result), "message": result or "Couldn't apply that action."})


@app.route("/agent/clear", methods=["POST"])
def agent_clear():
    c = conn()
    c.execute("DELETE FROM agent_messages")
    c.commit(); c.close()
    flash("Chat cleared.", "ok")
    return redirect(url_for("agent_home"))


@app.route("/notifications")
def notifications():
    c = conn()
    rows = c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 100").fetchall()
    c.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
    c.commit(); c.close()
    return render_template("notifications.html", rows=rows)


@app.route("/settings/agent", methods=["POST"])
def settings_agent():
    c = conn()
    f = request.form
    for key, field in (("anthropic_api_key", "api_key"), ("agent_model", "model"),
                       ("telegram_bot_token", "tg_token"), ("telegram_chat_id", "tg_chat")):
        val = (f.get(field) or "").strip()
        if val == "clear":
            c.execute("DELETE FROM settings WHERE key=?", (key,))
            c.commit()
        elif val:
            db.set_setting(c, key, val)
    if f.get("test_telegram"):
        ok = agent.send_telegram(c, "✅ OG Agent is connected to your Telegram!")
        flash("Telegram test sent — check your phone." if ok
              else "Telegram test failed — check the token & chat id.", "ok")
    else:
        flash("OG Agent settings saved.", "ok")
    c.close()
    return redirect(url_for("settings"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    try:
        import backup
        backup.make_backup(quiet=True)
    except Exception as e:
        print("(backup skipped:", e, ")")
    ip = lan_ip()
    print("\n" + "=" * 56)
    print("  OG GAINZ COMMAND CENTER is running")
    print("  On this PC : http://localhost:5000")
    print(f"  On phone   : http://{ip}:5000   (same WiFi)")
    print("=" * 56 + "\n")
    if not os.environ.get("OG_NO_BROWSER"):
        import threading, webbrowser
        threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
