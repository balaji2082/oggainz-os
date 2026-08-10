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
    orders placed for that date are left untouched. A hand-added extra meal is
    kept too: like a trial, it was put on that date deliberately, so closing the
    day must not silently throw it away. Returns the number removed.
    """
    cur = conn.execute(
        "DELETE FROM deliveries WHERE date=? AND status='Pending' "
        "AND subscription_id IS NOT NULL AND COALESCE(is_extra,0)=0",
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
# Pause / freeze windows
# ---------------------------------------------------------------------------
# A subscription can be frozen for one or more date ranges (see subscription_pauses).
# While a window covers a day, that day's order is NOT generated and no meal is
# consumed (remaining meals carry forward). A window with no end_date is open-ended
# (paused until manually resumed). Keeping the window on file — even after it ends —
# is what stops the previously paused days from re-appearing as Pending orders.

def _today_iso():
    return date.today().strftime("%Y-%m-%d")


def is_paused_on(conn, sub_id, day_iso):
    """True if this subscription has a pause window covering `day_iso`."""
    return conn.execute(
        "SELECT 1 FROM subscription_pauses WHERE subscription_id=? AND start_date<=? "
        "AND (end_date IS NULL OR end_date>=?) LIMIT 1",
        (sub_id, day_iso, day_iso)).fetchone() is not None


def pauses_for(conn, sub_id):
    return conn.execute(
        "SELECT * FROM subscription_pauses WHERE subscription_id=? "
        "ORDER BY start_date DESC, id DESC", (sub_id,)).fetchall()


def current_pause(conn, sub_id, day_iso=None):
    """The pause window covering `day_iso` (default today), or None."""
    day_iso = day_iso or _today_iso()
    return conn.execute(
        "SELECT * FROM subscription_pauses WHERE subscription_id=? AND start_date<=? "
        "AND (end_date IS NULL OR end_date>=?) ORDER BY start_date DESC LIMIT 1",
        (sub_id, day_iso, day_iso)).fetchone()


def _purge_pending_in_range(conn, sub_id, start_iso, end_iso=None):
    """Drop still-Pending auto rows for a sub inside [start, end] (end open if None).
    Delivered/Skipped history is never touched."""
    if end_iso:
        conn.execute("DELETE FROM deliveries WHERE subscription_id=? AND status='Pending' "
                     "AND date>=? AND date<=?", (sub_id, start_iso, end_iso))
    else:
        conn.execute("DELETE FROM deliveries WHERE subscription_id=? AND status='Pending' "
                     "AND date>=?", (sub_id, start_iso))


def add_pause(conn, sub_id, start_iso, end_iso=None, reason=""):
    """Freeze a subscription for [start, end] (end None = open-ended). Removes any
    Pending orders already sitting inside the window so they stop showing."""
    conn.execute(
        "INSERT INTO subscription_pauses(subscription_id,start_date,end_date,reason,created_at) "
        "VALUES(?,?,?,?,?)", (sub_id, start_iso, end_iso or None, (reason or "").strip() or None,
                              db.now_iso()))
    _purge_pending_in_range(conn, sub_id, start_iso, end_iso)
    conn.commit()
    recompute_subscription(conn, sub_id)


def update_pause(conn, pause_id, start_iso, end_iso=None, reason=""):
    p = conn.execute("SELECT * FROM subscription_pauses WHERE id=?", (pause_id,)).fetchone()
    if not p:
        return
    sub_id = p["subscription_id"]
    conn.execute("UPDATE subscription_pauses SET start_date=?, end_date=?, reason=? WHERE id=?",
                 (start_iso, end_iso or None, (reason or "").strip() or None, pause_id))
    _purge_pending_in_range(conn, sub_id, start_iso, end_iso)
    conn.commit()
    recompute_subscription(conn, sub_id)


def delete_pause(conn, pause_id):
    p = conn.execute("SELECT subscription_id FROM subscription_pauses WHERE id=?",
                     (pause_id,)).fetchone()
    conn.execute("DELETE FROM subscription_pauses WHERE id=?", (pause_id,))
    conn.commit()
    if p:
        recompute_subscription(conn, p["subscription_id"])


def resume_subscription(conn, sub_id, resume_iso=None):
    """Resume meals from `resume_iso` (default today): trim the covering pause window
    so its last paused day is the day before, and cancel any window that starts on/after
    the resume date. Fully-past windows are left intact as history."""
    resume_iso = resume_iso or _today_iso()
    last_paused = (datetime.strptime(resume_iso, "%Y-%m-%d").date()
                   - timedelta(days=1)).strftime("%Y-%m-%d")
    for p in conn.execute(
            "SELECT * FROM subscription_pauses WHERE subscription_id=? "
            "AND (end_date IS NULL OR end_date>=?)", (sub_id, resume_iso)).fetchall():
        if p["start_date"] >= resume_iso:
            conn.execute("DELETE FROM subscription_pauses WHERE id=?", (p["id"],))   # not started yet
        elif p["start_date"] <= last_paused:
            conn.execute("UPDATE subscription_pauses SET end_date=? WHERE id=?",
                         (last_paused, p["id"]))                                       # trim to yesterday
        else:
            conn.execute("DELETE FROM subscription_pauses WHERE id=?", (p["id"],))     # would be empty
    conn.commit()
    recompute_subscription(conn, sub_id)


def refresh_pause_statuses(conn):
    """Re-sync the stored status of any subscription whose pause window has just
    started or just ended (so a fixed 1-week freeze flips back to Active on its own
    once the dates pass). Cheap: only touches rows that are out of step."""
    today_iso = _today_iso()
    changed = False
    for r in conn.execute("SELECT id, status FROM subscriptions").fetchall():
        paused = is_paused_on(conn, r["id"], today_iso)
        if paused and r["status"] != "Paused":
            recompute_subscription(conn, r["id"], commit=False); changed = True
        elif (not paused) and r["status"] == "Paused":
            recompute_subscription(conn, r["id"], commit=False); changed = True
    if changed:
        conn.commit()


# ---------------------------------------------------------------------------
# Slot moves (temporary Lunch <-> Dinner for a date range)
# ---------------------------------------------------------------------------
def slot_moves_for(conn, sub_id):
    return conn.execute(
        "SELECT * FROM subscription_slot_moves WHERE subscription_id=? "
        "ORDER BY start_date DESC, id DESC", (sub_id,)).fetchall()


def current_slot_move(conn, sub_id, day_iso=None):
    day_iso = day_iso or _today_iso()
    return conn.execute(
        "SELECT * FROM subscription_slot_moves WHERE subscription_id=? AND start_date<=? "
        "AND (end_date IS NULL OR end_date>=?) ORDER BY start_date DESC LIMIT 1",
        (sub_id, day_iso, day_iso)).fetchone()


def effective_slot(conn, sub_id, day_iso, base_slot):
    """The slot a subscription delivers in on `day_iso`: a covering slot-move wins,
    otherwise the subscription's own standing slot."""
    mv = conn.execute(
        "SELECT slot FROM subscription_slot_moves WHERE subscription_id=? AND start_date<=? "
        "AND (end_date IS NULL OR end_date>=?) ORDER BY start_date DESC LIMIT 1",
        (sub_id, day_iso, day_iso)).fetchone()
    return (mv["slot"] if mv else None) or base_slot or "Lunch"


def resync_order_slots(conn, sub_id):
    """Re-point order rows at their effective slot after a slot move / base-slot change.

    Applies to TODAY and every future row (whatever their status) so a change shows up
    on the current day immediately — even if today's order was already marked
    delivered/skipped — plus any still-Pending past row. Genuinely past Delivered/Skipped
    rows are left untouched as the true record of what happened. Hand-added extras
    keep the slot they were created in — an extra put in Dinner on purpose must not
    be dragged back to the standing Lunch slot by an unrelated edit."""
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return
    today_iso = _today_iso()
    for r in conn.execute(
            "SELECT id, date FROM deliveries WHERE subscription_id=? "
            "AND (date>=? OR status='Pending') AND COALESCE(is_extra,0)=0",
            (sub_id, today_iso)).fetchall():
        # plan_for_day folds in the weekly plan's per-day slot as well as any
        # covering slot move, so a "Mon lunch / Wed dinner" schedule resyncs right.
        conn.execute("UPDATE deliveries SET meal_type=? WHERE id=?",
                     (plan_for_day(conn, sub, r["date"])["slot"], r["id"]))
    conn.commit()


def add_slot_move(conn, sub_id, slot, start_iso, end_iso=None, reason=""):
    conn.execute(
        "INSERT INTO subscription_slot_moves(subscription_id,start_date,end_date,slot,reason,created_at) "
        "VALUES(?,?,?,?,?,?)", (sub_id, start_iso, end_iso or None, slot,
                                (reason or "").strip() or None, db.now_iso()))
    conn.commit()
    resync_order_slots(conn, sub_id)


def update_slot_move(conn, move_id, slot, start_iso, end_iso=None, reason=""):
    mv = conn.execute("SELECT subscription_id FROM subscription_slot_moves WHERE id=?",
                      (move_id,)).fetchone()
    if not mv:
        return
    conn.execute("UPDATE subscription_slot_moves SET slot=?, start_date=?, end_date=?, reason=? "
                 "WHERE id=?", (slot, start_iso, end_iso or None,
                                (reason or "").strip() or None, move_id))
    conn.commit()
    resync_order_slots(conn, mv["subscription_id"])


def delete_slot_move(conn, move_id):
    mv = conn.execute("SELECT subscription_id FROM subscription_slot_moves WHERE id=?",
                      (move_id,)).fetchone()
    conn.execute("DELETE FROM subscription_slot_moves WHERE id=?", (move_id,))
    conn.commit()
    if mv:
        resync_order_slots(conn, mv["subscription_id"])


# ---------------------------------------------------------------------------
# Per-subscription delivery schedule (which weekdays, which slot, which box)
# ---------------------------------------------------------------------------
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def schedule_mode(sub):
    """'daily' | 'weekly' | 'ondemand'. Anything unrecognised (or a row from
    before this shipped) means daily — the behaviour every pack has always had."""
    try:
        mode = sub["schedule_mode"]
    except (IndexError, KeyError):
        mode = None
    return mode if mode in ("daily", "weekly", "ondemand") else "daily"


def day_plan_for(conn, sub_id):
    """{weekday:int -> row} for a subscription. Empty when it has no plan."""
    return {r["weekday"]: r for r in conn.execute(
        "SELECT * FROM subscription_day_plan WHERE subscription_id=? ORDER BY weekday",
        (sub_id,)).fetchall()}


def save_day_plan(conn, sub_id, rows):
    """Replace a subscription's weekly plan.

    rows = [{weekday, active, slot, variant_id, meals, note}]. Written as a whole
    so the stored plan always matches the grid that was submitted — no orphan
    weekday left behind from a previous save.
    """
    conn.execute("DELETE FROM subscription_day_plan WHERE subscription_id=?", (sub_id,))
    for r in rows:
        try:
            wd = int(r.get("weekday"))
        except (TypeError, ValueError):
            continue
        if not 0 <= wd <= 6:
            continue
        conn.execute(
            "INSERT INTO subscription_day_plan(subscription_id,weekday,active,slot,variant_id,meals,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (sub_id, wd, 1 if r.get("active") else 0, (r.get("slot") or None),
             (int(r["variant_id"]) if r.get("variant_id") else None),
             max(1, int(r.get("meals") or 1)), (r.get("note") or "").strip() or None))
    conn.commit()


def plan_for_day(conn, sub, day_iso):
    """What this subscription gets on this date:
        {deliver: bool, slot: str, variant_id: int|None, meals: int}

    The single place that answers "is there an order today, and which box". The
    generator, the backfill and the week planner all go through it so they can
    never disagree.
    """
    base_slot = effective_slot(conn, sub["id"], day_iso, sub["slot"])
    mode = schedule_mode(sub)
    if mode == "ondemand":
        # Nothing is ever generated: these customers confirm a day at a time and
        # the days are entered by hand (week planner / "add a meal").
        return {"deliver": False, "slot": base_slot, "variant_id": None, "meals": 1}
    if mode == "daily":
        return {"deliver": True, "slot": base_slot, "variant_id": None, "meals": 1}

    weekday = datetime.strptime(day_iso, "%Y-%m-%d").date().weekday()
    row = day_plan_for(conn, sub["id"]).get(weekday)
    if row is None:
        # 'weekly' with no row for this weekday = no delivery. A plan that only
        # ticks Mon/Wed/Fri stores only those three days, so a missing row is a
        # deliberate "not this day", not missing data.
        return {"deliver": False, "slot": base_slot, "variant_id": None, "meals": 1}
    # A temporary slot move still wins over the plan's per-day slot: it is the more
    # specific, more recent instruction ("deliver in the evening this week").
    mv = current_slot_move(conn, sub["id"], day_iso)
    slot = mv["slot"] if mv else ((row["slot"] or None) or base_slot)
    return {"deliver": bool(row["active"]), "slot": slot,
            "variant_id": row["variant_id"], "meals": max(1, row["meals"] or 1)}


def schedule_summary(conn, sub):
    """One line for lists and the subscription header, e.g.
    'Mon, Wed, Fri' or 'Mon Signature · Tue Salad · …' or 'On demand'."""
    mode = schedule_mode(sub)
    if mode == "daily":
        return "Every kitchen day"
    if mode == "ondemand":
        return "On demand — days confirmed one at a time"
    plan = day_plan_for(conn, sub["id"])
    days = [wd for wd in range(7) if plan.get(wd) is not None and plan[wd]["active"]]
    if not days:
        return "No delivery days set"
    names = {v["id"]: v["name"] for v in conn.execute(
        "SELECT id,name FROM subscription_variants WHERE subscription_id=?",
        (sub["id"],)).fetchall()}
    if any(plan[wd]["variant_id"] for wd in days):
        return " · ".join(
            f"{WEEKDAY_NAMES[wd]} {names.get(plan[wd]['variant_id'], sub['plan_name'] or 'usual')}"
            for wd in days)
    return ", ".join(WEEKDAY_NAMES[wd] for wd in days)


def resync_scheduled_days(conn, sub_id):
    """After the schedule changes, bring FUTURE planned orders in line with it.

    Removes still-Pending rows on days the subscription no longer delivers, and
    restamps the slot/box on the ones it keeps. Deliberately limited to today and
    later, and to Pending rows: a delivered meal is history and is never rewritten
    by a schedule edit. Hand-added extras are left alone — they were a decision.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return 0
    today_iso = _today_iso()
    removed = 0
    for r in conn.execute(
            "SELECT id, date FROM deliveries WHERE subscription_id=? AND status='Pending' "
            "AND date>=? AND COALESCE(is_extra,0)=0", (sub_id, today_iso)).fetchall():
        p = plan_for_day(conn, sub, r["date"])
        if not p["deliver"]:
            conn.execute("DELETE FROM deliveries WHERE id=?", (r["id"],))
            removed += 1
        else:
            conn.execute("UPDATE deliveries SET meal_type=?, variant_id=? WHERE id=?",
                         (p["slot"], p["variant_id"], r["id"]))
    conn.commit()
    return removed


# ---------------------------------------------------------------------------
# Subscription recompute
# ---------------------------------------------------------------------------
def recompute_subscription(conn, sub_id, commit=True):
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return
    # An extra meal the customer drew from their own pack counts exactly like any
    # other delivered meal — that is the whole point of it, and why a pack with an
    # extra finishes a day early. Only a meal explicitly sold OUTSIDE the pack
    # (counts_against_pack=0) is left out of the count. COALESCE keeps every row
    # written before this column existed counting as it always did.
    row = conn.execute(
        "SELECT "
        " SUM(status='Delivered' AND COALESCE(counts_against_pack,1)=1) AS d, "
        " SUM(status='Skipped'   AND COALESCE(counts_against_pack,1)=1) AS s, "
        " MAX(CASE WHEN status='Delivered' THEN date END) AS last "
        "FROM deliveries WHERE subscription_id=?",
        (sub_id,),
    ).fetchone()
    delivered = row["d"] or 0
    skipped = row["s"] or 0
    scheduled = sub["total_meals_scheduled"] or 0
    remaining = max(scheduled - delivered, 0)

    # Pause is now driven by date windows (subscription_pauses): the sub shows as
    # Paused only while a window covers *today*. A finished freeze flips back to its
    # real state on its own; an open-ended freeze stays Paused until resumed.
    if is_paused_on(conn, sub_id, _today_iso()):
        status = "Paused"
    elif scheduled and delivered >= scheduled:
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
    # The pack owes nothing any more → clear every still-Pending auto row (today,
    # future, or stale past ones). This is what removes the duplicate orders that
    # appear when past days are temporarily unmarked (sub flips back to Active,
    # new day rows get generated) and then re-marked Delivered: the moment the
    # count is complete again, the extra Pending rows vanish on their own.
    # Delivered/Skipped history is never touched.
    # A hand-added extra is never cleared here: it was put there on purpose, and if
    # it is what finished the pack off, deleting it would undo the very meal that
    # completed the count.
    if scheduled and remaining <= 0:
        conn.execute("DELETE FROM deliveries WHERE subscription_id=? AND status='Pending' "
                     "AND COALESCE(is_extra,0)=0", (sub_id,))
    if commit:
        conn.commit()


def recompute_all(conn):
    for r in conn.execute("SELECT id FROM subscriptions").fetchall():
        recompute_subscription(conn, r["id"], commit=False)
    conn.commit()


def backfill_sub_days(conn, sub_id):
    """Create the missing order rows for a subscription from its start date up to
    TODAY. Called when a subscription is created/edited/renewed, so a pack that is
    registered late (meals already handed over on previous days but never marked —
    e.g. billed today with a start date 4 days back) immediately shows those past
    days as Pending in Today → Unmarked past orders, instead of hiding until each
    old day is browsed by hand.

    Also removes still-Pending rows dated before the (possibly corrected) start
    date. Never creates more rows than the pack owes, skips non-operating days and
    freeze windows, and never touches Delivered/Skipped history. Returns the number
    of rows created."""
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub or not sub["start_date"]:
        return 0
    # A row before the start date can only be a stale auto-row from an earlier,
    # wrong start date — impossible days, so clear the Pending ones.
    conn.execute("DELETE FROM deliveries WHERE subscription_id=? AND status='Pending' "
                 "AND date<?", (sub_id, sub["start_date"]))
    scheduled = sub["total_meals_scheduled"] or 0
    created = 0
    if scheduled:
        try:
            d = datetime.strptime(str(sub["start_date"])[:10], "%Y-%m-%d").date()
        except ValueError:
            conn.commit()
            return 0
        today = date.today()
        while d <= today:
            day_iso = d.strftime("%Y-%m-%d")
            d += timedelta(days=1)
            # planned (Pending) + done (Delivered) rows must never exceed the pack
            used = conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE subscription_id=? "
                "AND status IN ('Pending','Delivered')", (sub_id,)).fetchone()[0]
            if used >= scheduled:
                break
            if not is_operating_day(conn, day_iso):
                continue
            if is_paused_on(conn, sub_id, day_iso):
                continue
            # Their own weekly schedule: a Mon/Wed/Fri customer gets no Tuesday
            # row, and an on-demand one gets nothing generated at all.
            plan = plan_for_day(conn, sub, day_iso)
            if not plan["deliver"]:
                continue
            # Only auto-rows block a re-run; a hand-added extra on this date must
            # not make the day look already-generated.
            if conn.execute("SELECT 1 FROM deliveries WHERE subscription_id=? AND date=? "
                            "AND COALESCE(is_extra,0)=0", (sub_id, day_iso)).fetchone():
                continue
            conn.execute(
                "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
                "status,month,notes,created_at,variant_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (day_iso, sub_id, sub["customer_id"], sub["customer_name"], plan["slot"],
                 "Pending", datetime.strptime(day_iso, "%Y-%m-%d").strftime("%b-%Y"),
                 sub["plan_name"], db.now_iso(), plan["variant_id"]))
            created += 1
    conn.commit()
    return created


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
    # Self-heal first: drop any Pending row on this day whose subscription owes
    # nothing any more (Completed / fully-delivered packs). Such rows are leftovers
    # from past-day corrections — e.g. a finished pack that briefly looked Active
    # while old days were unmarked — and must never keep showing as today's orders.
    conn.execute(
        "DELETE FROM deliveries WHERE date=? AND status='Pending' AND COALESCE(is_extra,0)=0 "
        "AND subscription_id IN "
        "(SELECT id FROM subscriptions WHERE COALESCE(total_meals_scheduled,0)>0 "
        " AND COALESCE(remaining_meals,0)<=0)", (day_iso,))
    month_label = datetime.strptime(day_iso, "%Y-%m-%d").strftime("%b-%Y")
    created = 0
    # Include Paused subs too: a sub frozen for a fixed range is still 'Paused' in the
    # stored status after its window ends, so we lean on is_paused_on(day) per-day to
    # decide. That way a finished freeze starts generating orders again automatically,
    # and an open-ended freeze keeps skipping until it is resumed.
    subs = conn.execute(
        "SELECT * FROM subscriptions WHERE status IN ('Active','Ending Soon','Paused') "
        "AND remaining_meals > 0 AND (start_date IS NULL OR start_date<=?)",
        (day_iso,),
    ).fetchall()
    for s in subs:
        if is_paused_on(conn, s["id"], day_iso):     # frozen this day → no order
            continue
        # Their own weekly schedule decides whether this weekday is a delivery day
        # at all, which slot it lands in, and which box gets cooked.
        plan = plan_for_day(conn, s, day_iso)
        if not plan["deliver"]:
            continue
        # An extra added by hand on this date is not an auto-row, so it must not
        # count as "already generated" and suppress the normal order.
        exists = conn.execute(
            "SELECT 1 FROM deliveries WHERE subscription_id=? AND date=? "
            "AND COALESCE(is_extra,0)=0",
            (s["id"], day_iso),
        ).fetchone()
        if exists:
            continue
        # Never PLAN more meals than the pack still owes: Pending rows already on
        # the books (any date) count against remaining, so browsing several future
        # days can't stack up extra orders for a nearly-finished subscription.
        planned = conn.execute(
            "SELECT COUNT(*) FROM deliveries WHERE subscription_id=? AND status='Pending'",
            (s["id"],)).fetchone()[0]
        if planned >= (s["remaining_meals"] or 0):
            continue
        conn.execute(
            "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,status,month,notes,created_at,variant_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (day_iso, s["id"], s["customer_id"], s["customer_name"],
             plan["slot"], "Pending", month_label, s["plan_name"], db.now_iso(),
             plan["variant_id"]),
        )
        created += 1
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
        "       c.lat, c.lng, c.zone, c.corridor, c.drop_type, c.map_note, "
        "       COALESCE(s.notes,'') AS sub_notes, "
        "       (SELECT COUNT(*) FROM customer_addresses ca "
        "          WHERE ca.customer_id=d.customer_id AND ca.active=1) AS n_addresses, "
        "       COALESCE(s.plan_name, d.notes) AS plan_name, s.remaining_meals, "
        "       t.id AS trial_ref, t.order_text AS trial_order "
        "FROM deliveries d "
        "LEFT JOIN customers c ON c.id=d.customer_id "
        "LEFT JOIN subscriptions s ON s.id=d.subscription_id "
        "LEFT JOIN trial_orders t ON t.id=d.trial_id "
        "WHERE d.date=? ORDER BY (d.meal_type='Dinner'), d.name",
        (day_iso,),
    ).fetchall()


def set_delivery_note(conn, delivery_id, text):
    """Save (or clear) the one-off note for a single day's delivery row.

    This is the per-order note for THAT date only — e.g. "deliver to dinner today",
    "swapped to Royal at same price", "no honey", "less oil – stomach upset". It stays
    on the row forever, so browsing that date (or the customer's delivery history) later
    shows exactly what happened. Empty text clears the note.
    """
    text = (text or "").strip() or None
    conn.execute(
        "UPDATE deliveries SET day_note=?, day_note_at=? WHERE id=?",
        (text, db.now_iso() if text else None, delivery_id),
    )
    conn.commit()
    return text


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
# Extra meals — a second box on a day, drawn from the pack already bought
# ---------------------------------------------------------------------------
def add_extra_meal(conn, sub_id, day_iso, slot=None, counts_against_pack=True,
                   note=None, variant_id=None, status="Pending"):
    """Put an ADDITIONAL meal on a date for a subscription that already has one.

    A subscription is sold as a number of servings, not as a number of days, so a
    customer can quite legitimately take two in one day — a second box in the
    evening, or a spare for someone at home — and have it come out of the pack
    they have already paid for. Buying that meal on its own would cost them more
    than their per-meal pack rate, which is exactly why they ask for it this way.

    The row is marked is_extra so no automatic process ever creates or removes it,
    and counts_against_pack (the normal case) makes it consume one of the pack's
    meals — so 20 meals now finish one delivery day earlier, which is correct.

    Returns the new delivery id, or None if the subscription doesn't exist.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return None
    slot = slot or plan_for_day(conn, sub, day_iso)["slot"]
    cur = conn.execute(
        "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,status,"
        "month,notes,created_at,is_extra,counts_against_pack,variant_id,day_note,day_note_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (day_iso, sub_id, sub["customer_id"], sub["customer_name"], slot, status,
         datetime.strptime(day_iso, "%Y-%m-%d").strftime("%b-%Y"), sub["plan_name"],
         db.now_iso(), 1, 1 if counts_against_pack else 0, variant_id,
         (note or "").strip() or None, db.now_iso() if (note or "").strip() else None))
    conn.commit()
    recompute_subscription(conn, sub_id)
    return cur.lastrowid


def delete_delivery(conn, delivery_id):
    """Remove one order row (used to undo an extra added by mistake) and put the
    subscription's counts back where they were."""
    d = conn.execute("SELECT subscription_id FROM deliveries WHERE id=?",
                     (delivery_id,)).fetchone()
    if not d:
        return None
    conn.execute("DELETE FROM deliveries WHERE id=?", (delivery_id,))
    conn.commit()
    if d["subscription_id"]:
        recompute_subscription(conn, d["subscription_id"])
    return d["subscription_id"]


def plan_week(conn, sub_id, wanted_days, window_days, slot=None):
    """Set exactly which days of a window an on-demand subscription eats on.

    Used by the week planner. `window_days` is every date the planner showed;
    `wanted_days` is the subset that is ticked. Both are needed: unticking a day
    means removing an order for a date that is, by definition, no longer in the
    ticked list, so the window is what tells us where to look.

    Delivered/Skipped days and hand-added extras are never touched, so re-saving
    a week you have already part-delivered is safe. Returns (added, removed).
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return 0, 0
    window = set(window_days or []) | set(wanted_days or [])
    wanted = set(wanted_days or []) & window
    added = removed = 0
    for day_iso in sorted(window):
        has = conn.execute(
            "SELECT id FROM deliveries WHERE subscription_id=? AND date=? AND status='Pending' "
            "AND COALESCE(is_extra,0)=0", (sub_id, day_iso)).fetchone()
        if day_iso in wanted and not has:
            if not is_operating_day(conn, day_iso) or is_paused_on(conn, sub_id, day_iso):
                continue
            # Never plan past what the pack still owes.
            planned = conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE subscription_id=? AND status='Pending'",
                (sub_id,)).fetchone()[0]
            if planned >= (sub["remaining_meals"] or 0):
                continue
            conn.execute(
                "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,status,"
                "month,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (day_iso, sub_id, sub["customer_id"], sub["customer_name"],
                 slot or effective_slot(conn, sub_id, day_iso, sub["slot"]), "Pending",
                 datetime.strptime(day_iso, "%Y-%m-%d").strftime("%b-%Y"),
                 sub["plan_name"], db.now_iso()))
            added += 1
        elif day_iso not in wanted and has:
            conn.execute("DELETE FROM deliveries WHERE id=?", (has["id"],))
            removed += 1
    conn.commit()
    recompute_subscription(conn, sub_id)
    return added, removed


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
    trial_received = g("SELECT COALESCE(SUM(amount),0) FROM trial_orders WHERE payment_status='Paid'")
    customers = g("SELECT COUNT(*) FROM customers")
    overall = sales + trial_sales                       # REVENUE = subscriptions + trials
    received_all = received + trial_received
    return {
        "active": active, "ending": ending, "unpaid_n": unpaid_n, "unpaid_amt": unpaid_amt,
        "sales": sales, "received": received, "pending_amt": sales - received,
        "trial_sales": trial_sales, "trial_received": trial_received,
        "overall": overall, "received_all": received_all,
        "pending_all": overall - received_all, "customers": customers,
    }


def outstanding_detail(conn):
    """Everything still owed, itemised so the Revenue 'To collect' box can drill down
    the same way the home page does: each unpaid subscription (with its pending amount)
    and each unpaid trial, both linking back to the order. Totals match
    headline_stats['pending_all']."""
    subs = []
    for r in conn.execute(
        "SELECT id, customer_id, customer_name, plan_name, "
        "       COALESCE(total_amount,0)-COALESCE(amount_received,0) AS pending, payment_status "
        "FROM subscriptions WHERE payment_status!='Paid' "
        "AND COALESCE(total_amount,0)-COALESCE(amount_received,0) > 0 "
        "ORDER BY pending DESC").fetchall():
        subs.append(dict(r))
    trials = []
    for r in conn.execute(
        "SELECT id, name, order_text, COALESCE(amount,0) AS pending "
        "FROM trial_orders WHERE payment_status!='Paid' AND COALESCE(amount,0) > 0 "
        "ORDER BY date DESC, id DESC").fetchall():
        trials.append(dict(r))
    sub_total = sum(s["pending"] for s in subs)
    trial_total = sum(t["pending"] for t in trials)
    return {"subs": subs, "trials": trials,
            "sub_total": sub_total, "trial_total": trial_total,
            "total": sub_total + trial_total,
            "sub_n": len(subs), "trial_n": len(trials)}


def month_label_iso(day_iso=None):
    d = datetime.strptime(day_iso, "%Y-%m-%d").date() if day_iso else date.today()
    return d.strftime("%b-%Y")


# ---------------------------------------------------------------------------
# Earned (delivered) revenue
# ---------------------------------------------------------------------------
# "Sales" for a day/week/month means the VALUE OF MEALS ACTUALLY DELIVERED in
# that period — NOT the lump subscription amount dumped on its start date. Each
# delivered meal is priced individually:
#   * a subscription meal  = its share of the pack  (pack total ÷ meals in pack)
#   * a trial meal          = its own trial price    (split if delivered over many)
# Summing those gives the real money earned that day. Per-meal values are rounded
# to whole rupees so the line items always add up to the day total on screen.

def _delivered_meal_rows(conn):
    """Every Delivered meal with the rupee value it earned. Single source of truth
    for the day/week/month earned-revenue views and the per-day drill-down."""
    rows = conn.execute(
        "SELECT d.id, d.date, d.meal_type, d.subscription_id, d.trial_id, "
        "       COALESCE(s.customer_name, t.name, d.name) AS name, "
        "       COALESCE(s.plan_name, t.order_text, d.notes) AS label, "
        "       s.subscription_price AS sub_price, s.delivery_fee AS sub_fee, "
        "       s.total_meals_scheduled AS sub_meals, "
        "       t.amount AS trial_amount "
        "FROM deliveries d "
        "LEFT JOIN subscriptions s ON s.id = d.subscription_id "
        "LEFT JOIN trial_orders  t ON t.id = d.trial_id "
        "WHERE d.status='Delivered' AND d.date IS NOT NULL").fetchall()

    # a trial may (rarely) be delivered over several meals — split its price evenly
    trial_counts = {}
    for r in rows:
        if r["trial_id"]:
            trial_counts[r["trial_id"]] = trial_counts.get(r["trial_id"], 0) + 1

    out = []
    for r in rows:
        # Split each delivered meal into food vs delivery so the views can show them
        # separately. A pack's delivery fee is the whole-pack figure, so the per-meal
        # delivery share is fee ÷ meals (e.g. ₹1200 / 20 = ₹60 a delivery).
        if r["subscription_id"]:
            meals = r["sub_meals"] or 0
            food = round((r["sub_price"] or 0) / meals) if meals else 0
            deliv = round((r["sub_fee"] or 0) / meals) if meals else 0
            source = "sub"
        elif r["trial_id"]:
            n = trial_counts.get(r["trial_id"], 1) or 1
            food = round((r["trial_amount"] or 0) / n)
            deliv = 0
            source = "trial"
        else:
            food, deliv, source = 0, 0, "other"
        out.append({
            "id": r["id"], "day": str(r["date"])[:10],
            "name": r["name"] or "—", "label": r["label"] or "",
            "meal_type": r["meal_type"] or "Lunch",
            "source": source, "value": food + deliv, "food": food, "delivery": deliv,
            "sub_id": r["subscription_id"], "trial_id": r["trial_id"],
        })
    return out


def _blank_bucket():
    return {"sub_sales": 0, "trial_sales": 0, "sub_meals": 0,
            "trial_meals": 0, "delivered": 0, "food": 0, "delivery": 0}


def _add_meal(bucket, m):
    bucket["delivered"] += 1
    bucket["food"] += m["food"]
    bucket["delivery"] += m["delivery"]
    if m["source"] == "trial":
        bucket["trial_sales"] += m["value"]; bucket["trial_meals"] += 1
    else:
        bucket["sub_sales"] += m["value"]; bucket["sub_meals"] += 1


def daily_view(conn, limit=30, month=None):
    """Build the whole day-wise revenue view in one pass over delivered meals.

    Returns (days, details, earned):
      days    – per-day buckets oldest→newest (for the chart + day list)
      details – {day: {items, total, sub_total, trial_total, sub_meals,
                       trial_meals, count}} for the per-day drill-down sheet
      earned  – earned totals across the (optionally month-scoped) rows

    `month` ('Mon-YYYY') scopes the whole view to one month; None = all time.
    """
    from collections import defaultdict
    rows = _filter_month(_delivered_meal_rows(conn), month)
    agg = defaultdict(_blank_bucket)
    items = defaultdict(list)
    earned = _blank_bucket()
    for m in rows:
        _add_meal(earned, m)
        _add_meal(agg[m["day"]], m)
        items[m["day"]].append(m)
    earned["overall"] = earned["sub_sales"] + earned["trial_sales"]

    days = []
    for k in sorted(agg.keys()):
        b = agg[k]; b["day"] = k; b["overall"] = b["sub_sales"] + b["trial_sales"]
        days.append(b)
    days = days[-limit:]

    details = {}
    for b in days:
        day_items = sorted(items[b["day"]],
                           key=lambda m: (m["meal_type"] == "Dinner", (m["name"] or "").lower()))
        details[b["day"]] = {
            "orders": day_items, "total": b["overall"],
            "sub_total": b["sub_sales"], "trial_total": b["trial_sales"],
            "sub_meals": b["sub_meals"], "trial_meals": b["trial_meals"],
            "food": b["food"], "delivery": b["delivery"],
            "count": b["delivered"],
        }
    return days, details, earned


def monthly_breakdown(conn):
    """One row per month: earned (delivered) sub/trial revenue + meals delivered,
    plus subs booked that month (context) and month-on-month growth on earned sales."""
    from collections import defaultdict
    earned = defaultdict(_blank_bucket)
    for m in _delivered_meal_rows(conn):
        label = datetime.strptime(m["day"], "%Y-%m-%d").strftime("%b-%Y")
        _add_meal(earned[label], m)

    g = lambda q, *a: (conn.execute(q, a).fetchone()[0] or 0)
    booked = [r["m"] for r in conn.execute(
        "SELECT month m FROM subscriptions WHERE month IS NOT NULL "
        "UNION SELECT month FROM trial_orders WHERE month IS NOT NULL").fetchall()]
    months = sorted(set(list(earned.keys()) + booked), key=_mkey)

    out, prev = [], None
    for mo in months:
        b = earned.get(mo, _blank_bucket())
        overall = b["sub_sales"] + b["trial_sales"]
        subs_sold = g("SELECT COUNT(*) FROM subscriptions WHERE month=?", mo)
        paid = g("SELECT COUNT(*) FROM subscriptions WHERE month=? AND payment_status='Paid'", mo)
        unpaid = g("SELECT COUNT(*) FROM subscriptions WHERE month=? AND payment_status!='Paid'", mo)
        growth = None if prev is None else ((overall - prev) / prev if prev else None)
        out.append({
            "month": mo, "sub_sales": b["sub_sales"], "trial_sales": b["trial_sales"],
            "overall": overall, "subs_sold": subs_sold, "paid": paid, "unpaid": unpaid,
            "delivered": b["delivered"], "growth": growth,
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


def weekly_breakdown(conn, limit=10, month=None):
    """Last `limit` weeks (Mon-start) of earned (delivered) sub + trial revenue.
    `month` ('Mon-YYYY') scopes to one month; None = all time."""
    from collections import defaultdict

    def wk(dstr):
        d = datetime.strptime(dstr[:10], "%Y-%m-%d").date()
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

    weeks = defaultdict(_blank_bucket)
    for m in _filter_month(_delivered_meal_rows(conn), month):
        _add_meal(weeks[wk(m["day"])], m)

    out = []
    for w in sorted(weeks.keys()):
        b = weeks[w]; b["week_start"] = w; b["overall"] = b["sub_sales"] + b["trial_sales"]
        out.append(b)
    return out[-limit:]


def daily_breakdown(conn, limit=30):
    """Per-day earned-revenue buckets (the chart rows). See daily_view for the
    full view incl. per-day drill-down details."""
    return daily_view(conn, limit)[0]


# ---------------------------------------------------------------------------
# Subscription SALES (packs booked)  —  vs. earned/delivered above
# ---------------------------------------------------------------------------
# The "earned" views above value the meals actually DELIVERED in a period. This
# section instead tracks the subscriptions actually SOLD (booked) in a period:
# how many packs were sold on a given day/week/month and their contract value.
# A sale is dated by booking_date (falling back to start_date when no booking
# date was recorded). This is the "actual subscription sales" Balaji asked for,
# kept deliberately separate from delivered-meal revenue.

def _period_key(day_iso, period):
    """Bucket key for a date: the date itself (day), its Monday (week), or its
    'Mon-YYYY' label (month). Day/week keys are ISO strings so they sort by time."""
    d = datetime.strptime(str(day_iso)[:10], "%Y-%m-%d").date()
    if period == "week":
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    if period == "month":
        return d.strftime("%b-%Y")
    return d.strftime("%Y-%m-%d")


def _filter_month(rows, month):
    """Keep only rows whose day falls in `month` ('Mon-YYYY'). None/'all' = no filter.
    Filtering the source rows (not the buckets) keeps day & week totals reconciling
    to the same month total."""
    if not month or month == "all":
        return rows
    return [r for r in rows if _period_key(r["day"], "month") == month]


def _subscription_sale_rows(conn):
    """Every subscription as a one-off 'sale' on the day it was booked."""
    rows = conn.execute(
        "SELECT id, customer_id, customer_name, plan_name, duration, "
        "       COALESCE(NULLIF(booking_date,''), start_date) AS sale_date, "
        "       COALESCE(total_amount,0)    AS value, "
        "       COALESCE(subscription_price,0) AS food, "
        "       COALESCE(delivery_fee,0)    AS delivery, "
        "       COALESCE(amount_received,0) AS received, "
        "       payment_status, status "
        "FROM subscriptions").fetchall()
    out = []
    for r in rows:
        if not r["sale_date"]:
            continue
        out.append({
            "id": r["id"], "customer_id": r["customer_id"],
            "day": str(r["sale_date"])[:10],
            "name": r["customer_name"] or "—", "plan": r["plan_name"] or "—",
            "duration": r["duration"] or "", "value": r["value"],
            "food": r["food"], "delivery": r["delivery"],
            "received": r["received"], "payment_status": r["payment_status"] or "Unpaid",
            "status": r["status"] or "",
        })
    return out


def _sales_blank():
    return {"count": 0, "value": 0, "received": 0, "food": 0, "delivery": 0}


def subs_sold_breakdown(conn, period, limit=None, month=None):
    """Subscription-sales buckets oldest→newest: [{key,count,value,food,delivery,received}].
    period in {'day','week','month'}. `month` scopes day/week to one month."""
    from collections import defaultdict
    buckets = defaultdict(_sales_blank)
    for s in _filter_month(_subscription_sale_rows(conn), month):
        b = buckets[_period_key(s["day"], period)]
        b["count"] += 1
        b["value"] += s["value"]
        b["food"] += s["food"]
        b["delivery"] += s["delivery"]
        b["received"] += s["received"]
    keys = sorted(buckets.keys(), key=(_mkey if period == "month" else (lambda k: k)))
    out = []
    for k in keys:
        b = dict(buckets[k]); b["key"] = k
        out.append(b)
    return out[-limit:] if limit else out


def subs_sold_detail(conn, period, key):
    """The actual subscriptions sold in one specific day/week/month."""
    rows = [s for s in _subscription_sale_rows(conn) if _period_key(s["day"], period) == key]
    rows.sort(key=lambda s: (s["day"], (s["name"] or "").lower()))
    # NB: key is 'rows' (not 'items') — Jinja's attribute lookup would otherwise
    # resolve `.items` to dict.items rather than this list.
    return {"rows": rows, "count": len(rows),
            "value": sum(s["value"] for s in rows),
            "food": sum(s["food"] for s in rows),
            "delivery": sum(s["delivery"] for s in rows),
            "received": sum(s["received"] for s in rows)}


def monthly_revenue(conn):
    """ACTUAL revenue per month = subscriptions booked + trial sales that month.

    This is the real money sold (what Balaji calls revenue), as opposed to the
    day/week views which show a delivered-meal *estimate*. Subscriptions are dated
    by booking_date (→ start_date), trials by their own month. Each row also carries
    the food/delivery split of what was sold AND the value of meals actually
    *delivered* that month (the food-orders estimate), so the month view can show
    'subscriptions sold' next to 'food actually delivered'. Returns one row per
    month oldest→newest with month-on-month growth on revenue."""
    from collections import defaultdict
    subs = {b["key"]: b for b in subs_sold_breakdown(conn, "month")}
    trials = {}
    for r in conn.execute("SELECT month, COUNT(*) n, COALESCE(SUM(amount),0) amt, "
                          "COALESCE(SUM(CASE WHEN payment_status='Paid' THEN amount END),0) recv "
                          "FROM trial_orders WHERE month IS NOT NULL GROUP BY month").fetchall():
        trials[r["month"]] = {"n": r["n"], "amt": r["amt"], "recv": r["recv"]}

    # Value of meals actually delivered, bucketed by the month they were delivered in.
    delivered = defaultdict(_blank_bucket)
    for m in _delivered_meal_rows(conn):
        _add_meal(delivered[datetime.strptime(m["day"], "%Y-%m-%d").strftime("%b-%Y")], m)

    keys = sorted(set(list(subs.keys()) + list(trials.keys()) + list(delivered.keys())), key=_mkey)
    out, prev = [], None
    for k in keys:
        s = subs.get(k) or {}
        t = trials.get(k) or {}
        dl = delivered.get(k) or _blank_bucket()
        sub_value = s.get("value", 0) or 0
        trial_value = t.get("amt", 0) or 0
        revenue = sub_value + trial_value
        received = (s.get("received", 0) or 0) + (t.get("recv", 0) or 0)
        growth = None if prev is None else ((revenue - prev) / prev if prev else None)
        out.append({
            "month": k, "sub_value": sub_value, "sub_count": s.get("count", 0) or 0,
            "sub_food": s.get("food", 0) or 0, "sub_delivery": s.get("delivery", 0) or 0,
            "trial_value": trial_value, "trial_count": t.get("n", 0) or 0,
            "revenue": revenue, "received": received, "growth": growth,
            "deliv_value": dl["sub_sales"] + dl["trial_sales"], "deliv_meals": dl["delivered"],
            "deliv_food": dl["food"], "deliv_delivery": dl["delivery"],
        })
        prev = revenue
    return out


def delivered_detail(conn, period, key):
    """Delivered-meal breakdown for ONE specific day/week/month — for any date,
    not just the recent window. Mirrors the per-day drill-down totals so the
    revenue date-filter can show 'orders delivered + their unit price + total'."""
    rows = [m for m in _delivered_meal_rows(conn) if _period_key(m["day"], period) == key]
    rows.sort(key=lambda m: (m["day"], m["meal_type"] == "Dinner", (m["name"] or "").lower()))
    sub_total   = sum(m["value"] for m in rows if m["source"] != "trial")
    trial_total = sum(m["value"] for m in rows if m["source"] == "trial")
    return {
        "orders": rows, "total": sub_total + trial_total,
        "sub_total": sub_total, "trial_total": trial_total,
        "food": sum(m["food"] for m in rows), "delivery": sum(m["delivery"] for m in rows),
        "sub_meals":   sum(1 for m in rows if m["source"] != "trial"),
        "trial_meals": sum(1 for m in rows if m["source"] == "trial"),
        "count": len(rows),
    }


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
        # every table that references subscriptions.id moves in lock-step
        CHILD_TABLES = ("deliveries", "subscription_pauses", "subscription_slot_moves",
                        "subscription_components", "component_rules", "invoices")
        for i, old in enumerate(ordered):          # pass 1: park everything in temp space
            tmp = f"__SEQ_{i}"
            for t in CHILD_TABLES:
                conn.execute(f"UPDATE {t} SET subscription_id=? WHERE subscription_id=?", (tmp, old))
            conn.execute("UPDATE subscriptions SET id=? WHERE id=?", (tmp, old))
        for i, old in enumerate(ordered):          # pass 2: temp -> final contiguous id
            tmp, new = f"__SEQ_{i}", target[old]
            for t in CHILD_TABLES:
                conn.execute(f"UPDATE {t} SET subscription_id=? WHERE subscription_id=?", (new, tmp))
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
    conn.execute("DELETE FROM subscription_pauses WHERE subscription_id=?", (sid,))
    conn.execute("DELETE FROM subscription_slot_moves WHERE subscription_id=?", (sid,))
    conn.execute("DELETE FROM subscription_components WHERE subscription_id=?", (sid,))
    conn.execute("DELETE FROM component_rules WHERE subscription_id=?", (sid,))
    # invoices stay (they're financial history) but stop pointing at a dead id
    conn.execute("UPDATE invoices SET subscription_id=NULL WHERE subscription_id=?", (sid,))
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
