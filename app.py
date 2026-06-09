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

import db
import core

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

# ---------------------------------------------------------------------------
# Login gate
# ---------------------------------------------------------------------------
# Protection switches ON only when OG_PASSWORD is set (we set it on the public
# server). With no password configured, the app behaves exactly as before for
# local use on your laptop — no login screen.
APP_PASSWORD = os.environ.get("OG_PASSWORD")
PUBLIC_ENDPOINTS = {"login", "static"}


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


@app.context_processor
def inject_globals():
    c = conn()
    stats = core.headline_stats(c)
    c.close()
    return {"g_stats": stats, "active_page": request.endpoint,
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
    prev_d = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    next_d = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    c.close()
    return render_template("today.html", d=d, s=summary, ending=ending, unpaid=unpaid,
                           prev_d=prev_d, next_d=next_d, view=view, override=override,
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
@app.route("/subscriptions")
def subscriptions():
    c = conn()
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
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not sub:
        c.close(); abort(404)
    cust = c.execute("SELECT * FROM customers WHERE id=?", (sub["customer_id"],)).fetchone()
    dels = c.execute("SELECT * FROM deliveries WHERE subscription_id=? ORDER BY date DESC", (sid,)).fetchall()
    c.close()
    return render_template("subscription_detail.html", sub=sub, cust=cust, dels=dels)


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
        c.execute(
            """INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,duration,notes,
                 slot,preferred_timing,booking_date,start_date,month,subscription_price,
                 delivery_fee,total_amount,amount_received,payment_status,total_meals_scheduled,
                 status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, f.get("customer_id"), cust["name"] if cust else f.get("customer_name"),
             f.get("plan_name"), duration, f.get("notes"), f.get("slot", "Lunch"),
             f.get("preferred_timing"), f.get("booking_date") or None,
             f.get("start_date") or None,
             (datetime.strptime(f["start_date"], "%Y-%m-%d").strftime("%b-%Y")
              if f.get("start_date") else core.month_label_iso()),
             price, fee, total, received,
             "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid"),
             scheduled, "Active", db.now_iso()))
        c.commit()
        core.recompute_subscription(c, sid)
        c.close()
        flash(f"Subscription {sid} created.", "ok")
        return redirect(url_for("subscription_detail", sid=sid))
    customers = c.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY name").fetchall()
    c.close()
    return render_template("subscription_form.html", customers=customers, plans=plans,
                           sub=None, today=date.today().strftime("%Y-%m-%d"))


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
        c.execute(
            """UPDATE subscriptions SET customer_id=?, customer_name=?, plan_name=?, duration=?,
                 notes=?, slot=?, preferred_timing=?, start_date=?, subscription_price=?,
                 delivery_fee=?, total_amount=?, amount_received=?, payment_status=?,
                 total_meals_scheduled=? WHERE id=?""",
            (cust["id"] if cust else sub["customer_id"],
             cust["name"] if cust else sub["customer_name"],
             f.get("plan_name"), f.get("duration"), f.get("notes"), f.get("slot"),
             f.get("preferred_timing"), f.get("start_date") or None, price, fee, total, received,
             "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid"),
             int(f.get("total_meals_scheduled") or sub["total_meals_scheduled"] or 20), sid))
        c.commit()
        core.recompute_subscription(c, sid)
        # Keep the denormalised name on any future delivery rows correct too.
        c.execute("UPDATE deliveries SET name=?, customer_id=? WHERE subscription_id=?",
                  (cust["name"] if cust else sub["customer_name"],
                   cust["id"] if cust else sub["customer_id"], sid))
        c.commit()
        c.close()
        flash(f"Subscription {sid} updated.", "ok")
        return redirect(url_for("subscription_detail", sid=sid))
    customers = c.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
    plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY name").fetchall()
    c.close()
    return render_template("subscription_form.html", customers=customers, plans=plans,
                           sub=sub, today=date.today().strftime("%Y-%m-%d"))


@app.route("/subscriptions/<sid>/status", methods=["POST"])
def subscription_status(sid):
    new = request.form.get("to")
    c = conn()
    if new in ("Paused", "Active", "Completed"):
        c.execute("UPDATE subscriptions SET status=? WHERE id=?", (new, sid))
        c.commit()
        if new != "Paused":
            core.recompute_subscription(c, sid)
    c.close()
    flash(f"Subscription {sid} set to {new}.", "ok")
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
    start = date.today().strftime("%Y-%m-%d")
    c.execute(
        """INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,duration,notes,
             slot,preferred_timing,booking_date,start_date,month,subscription_price,delivery_fee,
             total_amount,amount_received,payment_status,total_meals_scheduled,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (nid, old["customer_id"], old["customer_name"], old["plan_name"], old["duration"],
         old["notes"], old["slot"], old["preferred_timing"], start, start,
         core.month_label_iso(start), old["subscription_price"], old["delivery_fee"],
         old["total_amount"], 0, "Unpaid", old["total_meals_scheduled"], "Active", db.now_iso()))
    c.commit()
    core.recompute_subscription(c, nid)
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
    c.execute("""INSERT OR REPLACE INTO trial_orders(id,date,name,phone,order_text,amount,
                   payment_status,month,conversion,address,notes,delivery_date,meal_type,
                   delivery_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (tid, d, f.get("name"), f.get("phone"), f.get("order_text"),
               float(f.get("amount") or 0), f.get("payment_status", "Paid"),
               core.month_label_iso(d), f.get("conversion"), f.get("address"),
               f.get("notes"), dd, f.get("meal_type", "Lunch"),
               f.get("delivery_status", "Pending"), db.now_iso()))
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
    c.close()
    return render_template("trial_form.html", trial=None, today=date.today().strftime("%Y-%m-%d"))


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
    c.close()
    return render_template("trial_form.html", trial=trial, today=date.today().strftime("%Y-%m-%d"))


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
@app.route("/revenue")
def revenue():
    c = conn()
    period = request.args.get("period", "day")       # day | week | month
    months = core.monthly_breakdown(c)
    weeks = core.weekly_breakdown(c)
    days, day_details, earned = core.daily_view(c)
    stats = core.headline_stats(c)
    by_plan = c.execute(
        "SELECT plan_name, COUNT(*) n, COALESCE(SUM(total_amount),0) amt, "
        "       COALESCE(SUM(amount_received),0) recv "
        "FROM subscriptions GROUP BY plan_name ORDER BY amt DESC").fetchall()
    # Collection rate for the donut: how much of all billed money is actually in.
    collected = (stats["received"] / stats["sales"] * 100) if stats["sales"] else 0
    # This-month snapshot so the headline reflects current performance, not all-time only.
    this_month = core.month_label_iso()
    cur = next((m for m in months if m["month"] == this_month), None)
    c.close()
    return render_template("revenue.html", months=months, weeks=weeks, days=days,
                           day_details=day_details, earned=earned,
                           stats=stats, by_plan=by_plan, period=period,
                           collected=collected, cur=cur, this_month=this_month)


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
