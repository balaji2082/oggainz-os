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
# invoice_public + terms + brochure are customer-facing pages (token-protected /
# plain info), so they stay reachable without the app password.
PUBLIC_ENDPOINTS = {"login", "static", "invoice_public", "terms", "brochure_public"}


@app.before_request
def require_login():
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
            # Only follow same-site relative paths (never an external URL).
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("today")
            return redirect(nxt)
        error = "Wrong password — try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def conn():
    return db.get_db()


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
    c.close()
    return {"g_stats": stats, "g_due": due, "g_unread": unread,
            "g_agent_ready": agent_ready,
            "active_page": request.endpoint,
            "today": date.today().strftime("%Y-%m-%d"),
            "login_active": bool(APP_PASSWORD), "authed": session.get("authed"),
            "deployed": bool(APP_PASSWORD)}


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
    c.close()
    return render_template("today.html", d=d, s=summary, ending=ending, unpaid=unpaid,
                           prev_d=prev_d, next_d=next_d, view=view, override=override,
                           prep=prep, backlog=backlog, ticks=ticks,
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
    c.close()
    return render_template("deliveries.html", d=d, rows=rows, view=view, override=override,
                           operating=operating, prev_d=prev_d, next_d=next_d,
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
    c.close()
    return render_template("subscription_detail.html", sub=sub, cust=cust, dels=dels,
                           pauses=pauses, slot_moves=slot_moves, cur_pause=cur_pause,
                           cur_move=cur_move, comp=comp, rules=rules,
                           rule_labels=rule_labels, suggestions=suggestions,
                           components=components, fmt_qty=menu.fmt_qty,
                           today=date.today().strftime("%Y-%m-%d"))


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
        # A back-dated start (meals already delivered, sub registered late) gets its
        # past days as Pending rows right away → they show in the backlog to mark.
        core.backfill_sub_days(c, sid)
        _save_composition_from_form(c, sid, f)
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
    c.close()
    return render_template("subscription_form.html", customers=customers, plans=plans,
                           sub=None, today=date.today().strftime("%Y-%m-%d"),
                           preset_customer=request.args.get("customer"),
                           lead=request.args.get("lead"), menu_data=menu_data,
                           existing_comp=[])


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
        c.close()
        flash(f"Subscription {sid} updated.", "ok")
        return redirect(url_for("subscription_detail", sid=sid))
    customers = c.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY name").fetchall()
    menu_data = _menu_data(c)
    existing_comp = [{"id": r["component_id"], "qty": r["qty"], "kind": r["kind"],
                      "note": r["note"] or ""} for r in menu.sub_composition(c, sid)]
    c.close()
    return render_template("subscription_form.html", customers=customers, plans=plans,
                           sub=sub, today=date.today().strftime("%Y-%m-%d"),
                           preset_customer=None, lead=None, menu_data=menu_data,
                           existing_comp=existing_comp)


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
    core.recompute_subscription(c, nid)
    core.backfill_sub_days(c, nid)      # renewals can be back-dated too
    c.close()
    flash(f"Renewed {sid} → new subscription {nid}.", "ok")
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
    c.close()
    return render_template("customer_detail.html", cust=cust, subs=subs, spent=spent)


@app.route("/customers/new", methods=["GET", "POST"])
def customer_new():
    c = conn()
    if request.method == "POST":
        f = request.form
        cid = next_id(c, "customers", "C")
        c.execute("INSERT INTO customers(id,name,phone,locality,address,notes,created_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (cid, f.get("name"), f.get("phone"), f.get("locality"),
                   f.get("address"), f.get("notes"), db.now_iso()))
        c.commit(); c.close()
        flash(f"Customer {cid} added.", "ok")
        return redirect(url_for("customer_detail", cid=cid))
    c.close()
    return render_template("customer_form.html", cust=None)


@app.route("/customers/<cid>/edit", methods=["GET", "POST"])
def customer_edit(cid):
    c = conn()
    cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        c.close(); abort(404)
    if request.method == "POST":
        f = request.form
        c.execute("UPDATE customers SET name=?,phone=?,locality=?,address=?,notes=? WHERE id=?",
                  (f.get("name"), f.get("phone"), f.get("locality"), f.get("address"),
                   f.get("notes"), cid))
        c.commit(); c.close()
        flash("Customer updated.", "ok")
        return redirect(url_for("customer_detail", cid=cid))
    c.close()
    return render_template("customer_form.html", cust=cust)


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
    c.execute("""INSERT OR REPLACE INTO trial_orders(id,date,name,phone,order_text,amount,
                   payment_status,month,conversion,address,notes,delivery_date,meal_type,
                   delivery_status,plan_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (tid, d, f.get("name"), f.get("phone"), f.get("order_text"),
               float(f.get("amount") or 0), f.get("payment_status", "Paid"),
               core.month_label_iso(d), f.get("conversion"), f.get("address"),
               f.get("notes"), dd, f.get("meal_type", "Lunch"),
               f.get("delivery_status", "Pending"), plan_id, db.now_iso()))
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
    c.close()
    return render_template("settings.html", plans=plans, overrides=overrides,
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
