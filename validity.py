"""
OG Gainz — pack validity (meals expire on a date, or they don't).

Added Aug 2026 to close the [GAP] recorded in `og-gainz-app/docs/01 §7.2.1`:
`validity_days` has sat on the plans table since the brochure was written and
nothing has ever enforced it. This is what enforces it — and only when Balaji
turns it on.

WHY A GLOBAL SWITCH, AGAINST docs/01 §7.2.0's OWN [LAW]
--------------------------------------------------------
That section is emphatic: *"A global 'enforce validity' flag would be the
wrong shape... the software must not quietly break promises Balaji already
made."* Balaji asked for a global switch anyway, 12 Aug 2026, alongside the
skip/pause build — with the explicit shape: default OFF, turning it ON applies
to everyone, and he can still turn it OFF for one client.

Read literally that is two switches fighting over one pack. The design below
is what makes both true at once:

    subscriptions.enforce_validity   — the flag every reader already checks
                                         (customer_api, the app, this module)
    subscriptions.validity_override  — NULL | 'on' | 'off'
    settings.validity_enforced_globally

A pack with `validity_override` set has made an explicit decision and the
global switch never overrides it — that is the promise §7.2.0 protects. A pack
with no override *follows* the global switch, which is what makes the switch
worth having: flip it on and every pack Balaji has never touched picks up
validity at once, without a decision recorded for each one first.

`enforce_validity` is kept as the single column every existing reader checks
(`customer_api.py`, the request queue, doc 01's own spec) so nothing downstream
needed to change — `sync()` below is the only thing that writes it.

[LAW] Defaults preserve today's behaviour exactly. The global switch defaults
OFF and every existing subscription's override defaults NULL, so
`enforce_validity` computes to 0 for every pack that exists right now. Not one
of them starts expiring because this file shipped.
"""

from datetime import datetime, timedelta

import core
import db

MAX_WALK_DAYS = 400   # a broken operating-days config must not spin forever


def globally_enabled(conn):
    return (db.get_setting(conn, "validity_enforced_globally") or "0").strip() == "1"


def set_globally_enabled(conn, on, changed_by=None):
    """Flip the master switch, and bring every un-decided pack into line with it.

    Only subscriptions with `validity_override IS NULL` move — anything Balaji
    has explicitly turned on or off for one customer stays exactly as he left
    it. That is the whole point of the override column.
    """
    db.set_setting(conn, "validity_enforced_globally", "1" if on else "0")
    affected = conn.execute(
        "SELECT id FROM subscriptions WHERE validity_override IS NULL").fetchall()
    for row in affected:
        sync(conn, row["id"], changed_by=changed_by)
    return len(affected)


def plan_validity_days(conn, plan_name):
    """How many days a pack of this plan is good for, or None if undefined."""
    row = conn.execute(
        "SELECT validity_days FROM plans WHERE name=?", (plan_name,)).fetchone()
    return int(row["validity_days"]) if row and row["validity_days"] else None


def effective_enabled(conn, sub):
    """Does THIS subscription enforce validity, right now?"""
    override = sub["validity_override"]
    if override == "on":
        return True
    if override == "off":
        return False
    return globally_enabled(conn)


def compute_expiry(conn, start_iso, validity_days):
    """The date `validity_days` USABLE days from `start_iso` lands on.

    [RULE] docs/01 §7.2 — kitchen-closed days extend validity automatically.
    Walking forward one calendar day at a time and only counting down on days
    the kitchen actually runs is what makes that true by construction: a closed
    day advances the date without spending any of the customer's allowance,
    exactly like a paused day costs nothing (core.py's pause logic already
    works the same way, for the same reason).
    """
    day = datetime.strptime(start_iso, "%Y-%m-%d").date()
    remaining = validity_days
    for _ in range(MAX_WALK_DAYS):
        if remaining <= 0:
            return day.strftime("%Y-%m-%d")
        day += timedelta(days=1)
        if core.is_operating_day(conn, day.strftime("%Y-%m-%d")):
            remaining -= 1
    return day.strftime("%Y-%m-%d")


def sync(conn, sub_id, changed_by=None):
    """Recompute `enforce_validity` (and `expires_on` if it needs one) for one pack.

    Called whenever the global switch moves, a per-subscription override
    changes, or a subscription's plan/start date changes. Idempotent — calling
    it twice in a row does nothing the second time.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub:
        return None

    enabled = effective_enabled(conn, sub)
    expires_on = sub["expires_on"]

    if enabled and not expires_on:
        # Turning on for the first time: compute it from the plan. A manually
        # set date (docs/01 §7.2.0 — "a plain date edit, always available") is
        # never overwritten here; this only fills in a blank.
        days = plan_validity_days(conn, sub["plan_name"])
        if days and sub["start_date"]:
            expires_on = compute_expiry(conn, sub["start_date"], days)

    conn.execute(
        "UPDATE subscriptions SET enforce_validity=?, expires_on=? WHERE id=?",
        (1 if enabled else 0, expires_on, sub_id))
    conn.commit()
    core.recompute_subscription(conn, sub_id)
    return enabled


def set_override(conn, sub_id, override, changed_by, expires_on=None, note=None):
    """Balaji's per-subscription decision.

    `override`: 'on' | 'off' | None (None = go back to following the global
    switch). `expires_on`, if given, is a direct date edit — always available,
    no confirmation needed, per docs/01 §7.2.0 — and is never recomputed once
    Balaji has set it by hand.
    """
    if override not in ("on", "off", None):
        raise ValueError("override must be 'on', 'off', or None")

    conn.execute(
        "UPDATE subscriptions SET validity_override=?, validity_changed_at=?, "
        "validity_changed_by=? WHERE id=?",
        (override, db.now_iso(), changed_by, sub_id))
    if note is not None:
        conn.execute("UPDATE subscriptions SET validity_note=? WHERE id=?",
                     ((note or "").strip() or None, sub_id))
    if expires_on:
        conn.execute("UPDATE subscriptions SET expires_on=? WHERE id=?",
                     (expires_on, sub_id))
    conn.commit()
    return sync(conn, sub_id, changed_by=changed_by)


def is_expired(conn, sub, today_iso=None):
    """True when this pack's window has closed with meals still unclaimed.

    A pack that finished its meals before its date runs out is Completed, not
    Expired — those are different facts and the status must not conflate them.
    """
    if not sub["enforce_validity"] or not sub["expires_on"]:
        return False
    today_iso = today_iso or datetime.now().strftime("%Y-%m-%d")
    remaining = sub["remaining_meals"] or 0
    return today_iso > sub["expires_on"] and remaining > 0


def days_left(conn, sub, today_iso=None):
    """Whole days until expiry, for the customer's own countdown. None if not enforced."""
    if not sub["enforce_validity"] or not sub["expires_on"]:
        return None
    today_iso = today_iso or datetime.now().strftime("%Y-%m-%d")
    today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    exp = datetime.strptime(sub["expires_on"], "%Y-%m-%d").date()
    return (exp - today).days


def summary(conn):
    """One row per live subscription, for the admin validity screen."""
    rows = conn.execute(
        "SELECT s.*, c.name AS customer_name FROM subscriptions s "
        "LEFT JOIN customers c ON c.id = s.customer_id "
        "WHERE s.status NOT IN ('Completed') "
        "ORDER BY (s.validity_override IS NOT NULL) DESC, s.expires_on IS NULL, s.expires_on"
    ).fetchall()
    out = []
    for s in rows:
        out.append({
            "subscription_id": s["id"],
            "customer_id": s["customer_id"],
            "customer_name": s["customer_name"],
            "plan": s["plan_name"],
            "status": s["status"],
            "enforced": bool(s["enforce_validity"]),
            "override": s["validity_override"],
            "expires_on": s["expires_on"],
            "days_left": days_left(conn, s),
            "expired": is_expired(conn, s),
            "remaining_meals": s["remaining_meals"],
            "note": s["validity_note"],
            "changed_at": s["validity_changed_at"],
            "changed_by": s["validity_changed_by"],
        })
    return out
