"""
OG Gainz — who may skip a day, and who may pause a stretch.

[LAW] Balaji, 25 Aug 2026. Built deliberately to the same shape as validity.py,
because that is how he asked for it: "likewise, make an option to just enable or
disable skip/pause for a particular client or particular subscription".

WHAT THIS REPLACES, AND WHY
---------------------------
Pausing used to be gated by `freeze_eligible_plans` — a comma-separated list of
plan NAMES, set to "Elite Meal Pack,Athlete Meal Pack". Two problems, both real:

  1. It answered the wrong question. It could say "Elite packs may pause"; it
     could never say "this customer may pause". A rule about plans, when the
     thing that actually varies is the person and the conversation he had with
     them.
  2. It was invisible until it fired. A Protein Shake customer was offered the
     button, picked dates, confirmed, and only then was refused.

So: one switch for everyone, and one override per pack that beats it. Exactly
the validity model, which already works and which Balaji already understands —
a second mental model for the same shape of decision is a tax he pays forever.

SKIP AND PAUSE TOGETHER, ON PURPOSE
-----------------------------------
One control covers both. They are the same promise to a customer ("I can take a
day off"), the same carry-forward mechanic underneath, and splitting them would
mean explaining a state where you may skip Tuesday but not pause Tuesday to
Thursday. If that distinction is ever genuinely needed, this is the one file
that has to change.

Pure functions over a connection and a subscription row — no Flask, no request
context — so both sides of the boundary can be tested directly.
"""

import db

# The only three values the override may hold. NULL/None means "not decided",
# which resolves to the global switch.
VALID_OVERRIDES = ("on", "off", None)


def globally_enabled(conn):
    """The master switch. Defaults ON — see APP_SETTINGS_DEFAULTS for why.

    Read as "anything that is not explicitly off is on", so a missing or
    malformed setting leaves customers able to skip rather than silently
    stripping the feature from everyone. Validity defaults the other way for
    the opposite reason: there, the unsafe direction is enforcing an expiry
    nobody agreed to.
    """
    raw = (db.get_setting(conn, "flex_enabled_globally") or "1").strip()
    return raw not in ("0", "", "false", "no")


def set_globally_enabled(conn, on, changed_by=None):
    """Flip the master switch. Returns how many packs actually move.

    Only subscriptions with `flex_override IS NULL` follow it — anything Balaji
    has explicitly turned on or off for one customer stays exactly as he left
    it. That is the entire point of the override column, and it is the same
    guarantee validity.set_globally_enabled makes.
    """
    db.set_setting(conn, "flex_enabled_globally", "1" if on else "0")
    n = conn.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE flex_override IS NULL").fetchone()[0]
    conn.commit()
    return n


def effective_enabled(conn, sub):
    """May THIS pack skip and pause, right now?

    `sub` may be a row or a mapping. Accessed defensively because this is called
    from the write path on databases that may not have been migrated yet — a
    missing column must read as "not decided", never as "refused".
    """
    try:
        override = sub["flex_override"]
    except (KeyError, IndexError, TypeError):
        override = None
    if override == "on":
        return True
    if override == "off":
        return False
    return globally_enabled(conn)


def set_override(conn, sub_id, override, changed_by, note=None):
    """Balaji's decision for one pack.

    `override`: 'on' | 'off' | None (None = go back to following the switch).
    """
    if override not in VALID_OVERRIDES:
        raise ValueError("override must be 'on', 'off', or None")
    conn.execute(
        "UPDATE subscriptions SET flex_override=?, flex_changed_at=?, "
        "flex_changed_by=? WHERE id=?",
        (override, db.now_iso(), changed_by, sub_id))
    if note is not None:
        conn.execute("UPDATE subscriptions SET flex_note=? WHERE id=?",
                     ((note or "").strip() or None, sub_id))
    conn.commit()
    row = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return effective_enabled(conn, row) if row else False


def summary(conn):
    """Every live pack and whether it may skip/pause — for the admin screen."""
    rows = conn.execute(
        "SELECT s.*, c.name AS customer_name FROM subscriptions s "
        "LEFT JOIN customers c ON c.id = s.customer_id "
        "WHERE s.status IN ('Active','Ending Soon','Paused') "
        "ORDER BY c.name, s.id").fetchall()
    out = []
    for r in rows:
        out.append({
            "subscription_id": r["id"],
            "customer_id": r["customer_id"],
            "customer_name": r["customer_name"],
            "plan": r["plan_name"],
            "status": r["status"],
            "enabled": effective_enabled(conn, r),
            "override": r["flex_override"],
            "note": r["flex_note"],
            "changed_at": r["flex_changed_at"],
            "changed_by": r["flex_changed_by"],
        })
    return out


__all__ = ["globally_enabled", "set_globally_enabled", "effective_enabled",
           "set_override", "summary", "VALID_OVERRIDES"]
