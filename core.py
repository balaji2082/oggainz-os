"""
OG Gainz Command Center — business logic / automation engine.

Everything that the Excel sheet used to do by hand lives here:
  * recompute a subscription's delivered / skipped / remaining + status
  * auto-build "today's deliveries" from the active subscriptions
  * dashboard + revenue maths

The deliveries table is the single source of truth for meal counts; these
helpers derive everything else from it (verified to match Balaji's own sheet).
"""

from datetime import date, datetime, timedelta

import db

ACTIVE_STATES = ("Active", "Ending Soon")


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
def ending_soon_threshold(conn):
    return int(db.get_setting(conn, "ending_soon_threshold", "2"))


def operating_days(conn):
    """Weekday numbers the kitchen runs (Mon=0 .. Sun=6). Default Mon-Fri."""
    raw = db.get_setting(conn, "operating_weekdays", "0,1,2,3,4")
    return {int(x) for x in raw.split(",") if x.strip() != ""}


def is_holiday(conn, day_iso):
    return conn.execute("SELECT 1 FROM holidays WHERE date=?", (day_iso,)).fetchone() is not None


def day_override(conn, day_iso):
    """Returns True (forced open), False (forced closed), or None (no override)."""
    r = conn.execute("SELECT is_open FROM day_overrides WHERE date=?", (day_iso,)).fetchone()
    return None if r is None else bool(r["is_open"])


def set_day_override(conn, day_iso, is_open, reason=""):
    conn.execute(
        "INSERT INTO day_overrides(date,is_open,reason) VALUES(?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET is_open=excluded.is_open, reason=excluded.reason",
        (day_iso, 1 if is_open else 0, reason))
    conn.commit()


def clear_day_override(conn, day_iso):
    conn.execute("DELETE FROM day_overrides WHERE date=?", (day_iso,))
    conn.commit()


def purge_unworked_day(conn, day_iso):
    """
    Remove the auto-generated, still-Pending *subscription* meals for a day.

    Used when a day is closed or reset back to "kitchen off": opening a day
    auto-creates Pending rows for every active subscription, and those used to
    linger (and keep showing up) after the day was closed again. We only drop
    Pending subscription rows — Delivered/Skipped history and any explicit trial
    orders placed for that date are left untouched. Returns the number removed.
    """
    cur = conn.execute(
        "DELETE FROM deliveries WHERE date=? AND status='Pending' "
        "AND subscription_id IS NOT NULL",
        (day_iso,))
    conn.commit()
    return cur.rowcount


def is_operating_day(conn, day_iso):
    """A specific-date override always wins; otherwise use the weekday default + holidays."""
    ov = day_override(conn, day_iso)
    if ov is not None:
        return ov
    d = datetime.strptime(day_iso, "%Y-%m-%d").date()
    return d.weekday() in operating_days(conn) and not is_holiday(conn, day_iso)


# ---------------------------------------------------------------------------
# Subscription recompute
# ---------------------------------------------------------------------------
def recompute_subscription(conn, sub_id, commit=True):
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return
    row = conn.execute(
        "SELECT "
        " SUM(status='Delivered') AS d, "
        " SUM(status='Skipped')   AS s, "
        " MAX(CASE WHEN status='Delivered' THEN date END) AS last "
        "FROM deliveries WHERE subscription_id=?",
        (sub_id,),
    ).fetchone()
    delivered = row["d"] or 0
    skipped = row["s"] or 0
    scheduled = sub["total_meals_scheduled"] or 0
    remaining = max(scheduled - delivered, 0)

    status = sub["status"]
    if status != "Paused":  # never override a manual pause
        if scheduled and delivered >= scheduled:
            status = "Completed"
        elif 0 < remaining <= ending_soon_threshold(conn):
            status = "Ending Soon"
        else:
            status = "Active"

    conn.execute(
        "UPDATE subscriptions SET delivered_count=?, skipped_count=?, remaining_meals=?, "
        "last_delivery_date=?, status=? WHERE id=?",
        (delivered, skipped, remaining, row["last"], status, sub_id),
    )
    if commit:
        conn.commit()


def recompute_all(conn):
    for r in conn.execute("SELECT id FROM subscriptions").fetchall():
        recompute_subscription(conn, r["id"], commit=False)
    conn.commit()


# ---------------------------------------------------------------------------
# Today's deliveries (auto-generated)
# ---------------------------------------------------------------------------
def ensure_day_rows(conn, day_iso):
    """
    Make sure a Pending delivery row exists for every active subscription that
    still owes meals on an operating day. Returns the number created.
    Never creates duplicates (checks existing rows first).
    """
    if not is_operating_day(conn, day_iso):
        return 0
    month_label = datetime.strptime(day_iso, "%Y-%m-%d").strftime("%b-%Y")
    created = 0
    subs = conn.execute(
        "SELECT * FROM subscriptions WHERE status IN ('Active','Ending Soon') "
        "AND remaining_meals > 0 AND (start_date IS NULL OR start_date<=?)",
        (day_iso,),
    ).fetchall()
    for s in subs:
        exists = conn.execute(
            "SELECT 1 FROM deliveries WHERE subscription_id=? AND date=?",
            (s["id"], day_iso),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,status,month,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (day_iso, s["id"], s["customer_id"], s["customer_name"],
             s["slot"] or "Lunch", "Pending", month_label, s["plan_name"], db.now_iso()),
        )
        created += 1
    if created:
        conn.commit()
    return created


def ensure_trial_rows(conn, day_iso):
    """Put any trial scheduled for this day into the day's order list (as a delivery row)."""
    created = 0
    month_label = datetime.strptime(day_iso, "%Y-%m-%d").strftime("%b-%Y")
    for t in conn.execute("SELECT * FROM trial_orders WHERE delivery_date=?", (day_iso,)).fetchall():
        if conn.execute("SELECT 1 FROM deliveries WHERE trial_id=? AND date=?",
                        (t["id"], day_iso)).fetchone():
            continue
        conn.execute(
            "INSERT INTO deliveries(date,trial_id,name,meal_type,status,month,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (day_iso, t["id"], t["name"], t["meal_type"] or "Lunch",
             t["delivery_status"] or "Pending", month_label, t["order_text"], db.now_iso()))
        created += 1
    if created:
        conn.commit()
    return created


def sync_trial_delivery(conn, trial_id):
    """Keep a trial's single delivery row in step with its delivery_date/slot/status."""
    t = conn.execute("SELECT * FROM trial_orders WHERE id=?", (trial_id,)).fetchone()
    conn.execute("DELETE FROM deliveries WHERE trial_id=?", (trial_id,))
    if t and t["delivery_date"]:
        month_label = datetime.strptime(t["delivery_date"], "%Y-%m-%d").strftime("%b-%Y")
        conn.execute(
            "INSERT INTO deliveries(date,trial_id,name,meal_type,status,month,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (t["delivery_date"], trial_id, t["name"], t["meal_type"] or "Lunch",
             t["delivery_status"] or "Pending", month_label, t["order_text"], db.now_iso()))
    conn.commit()


def deliveries_for_day(conn, day_iso):
    if is_operating_day(conn, day_iso):
        ensure_day_rows(conn, day_iso)       # open day → make sure each active sub has a row
    else:
        # Closed/reset day: self-heal any leftover auto-generated Pending sub rows so a
        # day that was opened-to-peek then closed/reset never keeps showing phantom orders.
        purge_unworked_day(conn, day_iso)
    ensure_trial_rows(conn, day_iso)     # explicit trials may be scheduled on any day
    return conn.execute(
        "SELECT d.*, COALESCE(c.phone, t.phone) AS phone, c.locality, "
        "       COALESCE(c.address, t.address) AS address, s.preferred_timing, "
        "       COALESCE(s.notes,'') AS sub_notes, "
        "       COALESCE(s.plan_name, d.notes) AS plan_name, s.remaining_meals, "
        "       t.id AS trial_ref, t.order_text AS trial_order "
        "FROM deliveries d "
        "LEFT JOIN customers c ON c.id=d.customer_id "
        "LEFT JOIN subscriptions s ON s.id=d.subscription_id "
        "LEFT JOIN trial_orders t ON t.id=d.trial_id "
        "WHERE d.date=? ORDER BY (d.meal_type='Dinner'), d.name",
        (day_iso,),
    ).fetchall()


def mark_delivery(conn, delivery_id, status):
    d = conn.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
    if not d:
        return None
    conn.execute("UPDATE deliveries SET status=? WHERE id=?", (status, delivery_id))
    conn.commit()
    if d["subscription_id"]:
        recompute_subscription(conn, d["subscription_id"])
    if d["trial_id"]:                    # keep the trial's own delivery status in sync
        conn.execute("UPDATE trial_orders SET delivery_status=? WHERE id=?", (status, d["trial_id"]))
        conn.commit()
    return d["subscription_id"]


# ---------------------------------------------------------------------------
# Dashboard / stats
# ---------------------------------------------------------------------------
def today_summary(conn, day_iso):
    rows = deliveries_for_day(conn, day_iso)
    total = len(rows)
    done = sum(1 for r in rows if r["status"] == "Delivered")
    skipped = sum(1 for r in rows if r["status"] == "Skipped")
    pending = sum(1 for r in rows if r["status"] == "Pending")
    # cook counts by plan + meal type, also grouped by slot for a structured prep list
    cook = {}
    for r in rows:
        if r["status"] in ("Delivered", "Pending"):
            key = (r["plan_name"] or "—", r["meal_type"] or "—")
            cook[key] = cook.get(key, 0) + 1
    cook_flat = sorted(([p, m, n] for (p, m), n in cook.items()), key=lambda x: -x[2])
    cook_lunch = sorted(([p, n] for (p, m), n in cook.items() if m != "Dinner"), key=lambda x: -x[1])
    cook_dinner = sorted(([p, n] for (p, m), n in cook.items() if m == "Dinner"), key=lambda x: -x[1])
    return {
        "rows": rows, "total": total, "done": done, "skipped": skipped,
        "pending": pending, "operating": is_operating_day(conn, day_iso),
        "cook": cook_flat, "cook_lunch": cook_lunch, "cook_dinner": cook_dinner,
        "cook_total": sum(n for _, _, n in cook_flat),
        "lunch_total": sum(n for _, n in cook_lunch),
        "dinner_total": sum(n for _, n in cook_dinner),
    }


def headline_stats(conn):
    g = lambda q, *a: (conn.execute(q, a).fetchone()[0] or 0)
    active = g("SELECT COUNT(*) FROM subscriptions WHERE status IN ('Active','Ending Soon')")
    ending = g("SELECT COUNT(*) FROM subscriptions WHERE status='Ending Soon'")
    unpaid_n = g("SELECT COUNT(*) FROM subscriptions WHERE payment_status!='Paid'")
    unpaid_amt = g("SELECT COALESCE(SUM(total_amount-COALESCE(amount_received,0)),0) "
                   "FROM subscriptions WHERE payment_status!='Paid'")
    sales = g("SELECT COALESCE(SUM(total_amount),0) FROM subscriptions")
    received = g("SELECT COALESCE(SUM(amount_received),0) FROM subscriptions")
    trial_sales = g("SELECT COALESCE(SUM(amount),0) FROM trial_orders")
    customers = g("SELECT COUNT(*) FROM customers")
    return {
        "active": active, "ending": ending, "unpaid_n": unpaid_n, "unpaid_amt": unpaid_amt,
        "sales": sales, "received": received, "pending_amt": sales - received,
        "trial_sales": trial_sales, "overall": sales + trial_sales, "customers": customers,
    }


def month_label_iso(day_iso=None):
    d = datetime.strptime(day_iso, "%Y-%m-%d").date() if day_iso else date.today()
    return d.strftime("%b-%Y")


def monthly_breakdown(conn):
    """One row per month with sales, trials, orders delivered, growth %."""
    months = [r["m"] for r in conn.execute(
        "SELECT month m FROM subscriptions WHERE month IS NOT NULL "
        "UNION SELECT month FROM trial_orders WHERE month IS NOT NULL"
    ).fetchall()]
    # sort by real date
    def keyf(m):
        try:
            return datetime.strptime(m, "%b-%Y")
        except Exception:
            return datetime.min
    months = sorted(set(months), key=keyf)
    out = []
    prev = None
    g = lambda q, *a: (conn.execute(q, a).fetchone()[0] or 0)
    for m in months:
        sub_sales = g("SELECT COALESCE(SUM(total_amount),0) FROM subscriptions WHERE month=?", m)
        trial_sales = g("SELECT COALESCE(SUM(amount),0) FROM trial_orders WHERE month=?", m)
        subs_sold = g("SELECT COUNT(*) FROM subscriptions WHERE month=?", m)
        paid = g("SELECT COUNT(*) FROM subscriptions WHERE month=? AND payment_status='Paid'", m)
        unpaid = g("SELECT COUNT(*) FROM subscriptions WHERE month=? AND payment_status!='Paid'", m)
        delivered = g("SELECT COUNT(*) FROM deliveries WHERE month=? AND status='Delivered'", m)
        overall = sub_sales + trial_sales
        growth = None if not prev else (overall - prev) / prev if prev else None
        out.append({
            "month": m, "sub_sales": sub_sales, "trial_sales": trial_sales,
            "overall": overall, "subs_sold": subs_sold, "paid": paid, "unpaid": unpaid,
            "delivered": delivered, "growth": growth,
        })
        prev = overall
    return out


def _mkey(m):
    try:
        return datetime.strptime(m, "%b-%Y")
    except Exception:
        return datetime.min


def customer_analytics(conn):
    """
    Per month: how many customers were active, how many were NEW, how many were
    RETAINED from the previous month, and how many were LOST (churned) since the
    previous month. Returns names too, so lost customers can be won back.
    """
    from collections import defaultdict
    active = defaultdict(set)
    for r in conn.execute(
        "SELECT DISTINCT customer_id, month FROM deliveries "
        "WHERE customer_id IS NOT NULL AND month IS NOT NULL "
        "AND status IN ('Delivered','Skipped','Pending')").fetchall():
        active[r["month"]].add(r["customer_id"])
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM customers")}

    def people(ids):
        """Sorted [{id, name}] so the template can list and link each customer."""
        return sorted(({"id": i, "name": names.get(i, i)} for i in ids),
                      key=lambda p: (p["name"] or "").lower())

    months = sorted(active.keys(), key=_mkey)
    seen, prev, out = set(), None, []
    for m in months:
        cur = active[m]
        new = cur - seen
        retained = (cur & active[prev]) if prev else set()
        lost = (active[prev] - cur) if prev else set()
        out.append({
            "month": m, "active": len(cur), "new": len(new),
            "retained": len(retained), "lost": len(lost),
            "active_people": people(cur), "new_people": people(new),
            "retained_people": people(retained), "lost_people": people(lost),
            "new_names": sorted(names.get(i, i) for i in new),
            "lost_names": sorted(names.get(i, i) for i in lost),
        })
        seen |= cur
        prev = m
    return out


def weekly_breakdown(conn, limit=10):
    """Last `limit` weeks (Mon-start): subs started, sub sales, trials, trial sales, meals delivered."""
    from collections import defaultdict

    def wk(dstr):
        d = datetime.strptime(dstr[:10], "%Y-%m-%d").date()
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

    weeks = defaultdict(lambda: {"sub_sales": 0, "subs": 0, "trial_sales": 0,
                                 "trials": 0, "delivered": 0})
    for r in conn.execute("SELECT start_date, total_amount FROM subscriptions "
                          "WHERE start_date IS NOT NULL").fetchall():
        w = wk(r["start_date"]); weeks[w]["sub_sales"] += r["total_amount"] or 0; weeks[w]["subs"] += 1
    for r in conn.execute("SELECT date, amount FROM trial_orders WHERE date IS NOT NULL").fetchall():
        w = wk(r["date"]); weeks[w]["trial_sales"] += r["amount"] or 0; weeks[w]["trials"] += 1
    for r in conn.execute("SELECT date FROM deliveries WHERE status='Delivered' AND date IS NOT NULL").fetchall():
        w = wk(r["date"]); weeks[w]["delivered"] += 1

    out = []
    for w in sorted(weeks.keys()):
        d = weeks[w]
        d["week_start"] = w
        d["overall"] = d["sub_sales"] + d["trial_sales"]
        out.append(d)
    return out[-limit:]


def daily_breakdown(conn, limit=30):
    """
    Last `limit` operating-relevant days: subscription sales (by start date),
    trial sales (by booking date) and meals delivered (by delivery date).
    One row per day that had any activity, newest last (for charting).
    """
    from collections import defaultdict

    days = defaultdict(lambda: {"sub_sales": 0, "subs": 0, "trial_sales": 0,
                                "trials": 0, "delivered": 0})
    for r in conn.execute("SELECT start_date d, total_amount a FROM subscriptions "
                          "WHERE start_date IS NOT NULL").fetchall():
        k = str(r["d"])[:10]
        days[k]["sub_sales"] += r["a"] or 0
        days[k]["subs"] += 1
    for r in conn.execute("SELECT date d, amount a FROM trial_orders WHERE date IS NOT NULL").fetchall():
        k = str(r["d"])[:10]
        days[k]["trial_sales"] += r["a"] or 0
        days[k]["trials"] += 1
    for r in conn.execute("SELECT date d FROM deliveries WHERE status='Delivered' AND date IS NOT NULL").fetchall():
        k = str(r["d"])[:10]
        days[k]["delivered"] += 1

    out = []
    for k in sorted(days.keys()):
        d = days[k]
        d["day"] = k
        d["overall"] = d["sub_sales"] + d["trial_sales"]
        out.append(d)
    return out[-limit:]


# ---------------------------------------------------------------------------
# Delete + renumber subscriptions
# ---------------------------------------------------------------------------
def _sub_num(sid):
    digits = "".join(ch for ch in str(sid) if ch.isdigit())
    return int(digits) if digits else 0


def resequence_subscriptions(conn):
    """
    Re-number every subscription so the IDs are a gap-free S001, S002, ... run
    in their existing numeric order. Lower IDs keep their number; only IDs above
    a removed/gap position shift down. The matching deliveries.subscription_id
    values are moved in lock-step so no meal history is ever orphaned.

    Done in two passes through a temporary namespace so source and target IDs can
    overlap without colliding. Foreign-key checks are turned off only for the
    rename (re-enabled afterwards) because momentarily a parent/child pair is out
    of sync mid-rename, which is exactly what the two-pass swap fixes.
    """
    ordered = sorted((r["id"] for r in conn.execute("SELECT id FROM subscriptions")),
                     key=_sub_num)
    target = {old: f"S{i + 1:03d}" for i, old in enumerate(ordered)}
    if all(old == new for old, new in target.items()):
        return 0                                   # already contiguous — nothing to do

    conn.commit()                                  # make sure no transaction is open
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for i, old in enumerate(ordered):          # pass 1: park everything in temp space
            tmp = f"__SEQ_{i}"
            conn.execute("UPDATE deliveries SET subscription_id=? WHERE subscription_id=?", (tmp, old))
            conn.execute("UPDATE subscriptions SET id=? WHERE id=?", (tmp, old))
        for i, old in enumerate(ordered):          # pass 2: temp -> final contiguous id
            tmp, new = f"__SEQ_{i}", target[old]
            conn.execute("UPDATE deliveries SET subscription_id=? WHERE subscription_id=?", (new, tmp))
            conn.execute("UPDATE subscriptions SET id=? WHERE id=?", (new, tmp))
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return sum(1 for old, new in target.items() if old != new)


def delete_subscription(conn, sid):
    """
    Remove a subscription and its delivery rows, then renumber the rest so the IDs
    stay gap-free. Returns True if something was deleted.
    """
    if not conn.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
        return False
    conn.execute("DELETE FROM deliveries WHERE subscription_id=?", (sid,))
    conn.execute("DELETE FROM subscriptions WHERE id=?", (sid,))
    conn.commit()
    resequence_subscriptions(conn)
    return True


def trials_by_month(conn):
    rows = conn.execute(
        "SELECT month, COUNT(*) n, COALESCE(SUM(amount),0) amt, "
        "       SUM(CASE WHEN conversion IS NOT NULL AND conversion!='' THEN 1 ELSE 0 END) conv "
        "FROM trial_orders WHERE month IS NOT NULL GROUP BY month").fetchall()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: _mkey(r["month"]))
    return out
