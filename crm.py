"""
OG Gainz Command Center — CRM (lead tracking → conversion).

A self-contained module so it is easy for Balaji to edit later. It manages
"leads" (prospects) through a simple pipeline and keeps a timeline of updates
for each one. When a lead converts it can spin up a real customer (and link the
subscription that gets booked), so the CRM hands off cleanly to the rest of the
Command Center.

Pipeline stages (in order):
    New → Contacted → Trial → Negotiating → Won
                                         ↘ Lost
"""

from datetime import date, datetime

import db

# Pipeline definition -------------------------------------------------------
STAGES = ["New", "Contacted", "Trial", "Negotiating", "Won", "Lost"]
OPEN_STAGES = ["New", "Contacted", "Trial", "Negotiating"]   # still in play
WON, LOST = "Won", "Lost"

# Pickers shown in the form (kept here so they're easy to tweak).
SOURCES = ["Instagram", "WhatsApp", "Referral", "Friend", "Walk-in", "Ad", "Other"]
GOALS = ["Fat loss", "Muscle gain", "Maintenance", "General fitness", "Other"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _next_id(conn, table, prefix, width=3):
    rows = conn.execute(f"SELECT id FROM {table} WHERE id LIKE ?", (prefix + "%",)).fetchall()
    mx = 0
    for r in rows:
        tail = "".join(ch for ch in str(r["id"])[len(prefix):] if ch.isdigit())
        if tail:
            mx = max(mx, int(tail))
    return f"{prefix}{mx + 1:0{width}d}"


def _today():
    return date.today().strftime("%Y-%m-%d")


def get_lead(conn, lid):
    return conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()


def updates_for(conn, lid):
    return conn.execute(
        "SELECT * FROM lead_updates WHERE lead_id=? ORDER BY at DESC, id DESC", (lid,)
    ).fetchall()


def log_update(conn, lid, kind, text, commit=True):
    """Append a line to a lead's timeline and bump its last_contact / updated_at."""
    conn.execute(
        "INSERT INTO lead_updates(lead_id, at, kind, text) VALUES(?,?,?,?)",
        (lid, db.now_iso(), kind, text))
    conn.execute("UPDATE leads SET last_contact=?, updated_at=? WHERE id=?",
                 (_today(), db.now_iso(), lid))
    if commit:
        conn.commit()


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------
def save_lead(conn, f, lid=None):
    """Insert or update a lead from a form-like mapping `f`. Returns the lead id."""
    def num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    stage = (f.get("stage") or "New").strip() or "New"
    fields = dict(
        name=(f.get("name") or "").strip(),
        phone=f.get("phone"), source=f.get("source"), locality=f.get("locality"),
        goal=f.get("goal"), interest=f.get("interest"), stage=stage,
        est_value=num(f.get("est_value")),
        next_follow_up=(f.get("next_follow_up") or None),
        notes=f.get("notes"), lost_reason=(f.get("lost_reason") or None),
        trial_id=(f.get("trial_id") or None),
    )

    if lid:
        old = get_lead(conn, lid)
        conn.execute(
            """UPDATE leads SET name=?, phone=?, source=?, locality=?, goal=?, interest=?,
                 stage=?, est_value=?, next_follow_up=?, notes=?, lost_reason=?, trial_id=?,
                 updated_at=? WHERE id=?""",
            (fields["name"], fields["phone"], fields["source"], fields["locality"],
             fields["goal"], fields["interest"], fields["stage"], fields["est_value"],
             fields["next_follow_up"], fields["notes"], fields["lost_reason"],
             fields["trial_id"], db.now_iso(), lid))
        conn.commit()
        if old and old["stage"] != fields["stage"]:
            log_update(conn, lid, "Stage", f"Stage changed: {old['stage']} → {fields['stage']}")
        return lid

    lid = _next_id(conn, "leads", "L")
    conn.execute(
        """INSERT INTO leads(id, name, phone, source, locality, goal, interest, stage,
             est_value, next_follow_up, last_contact, notes, lost_reason, trial_id,
             created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (lid, fields["name"], fields["phone"], fields["source"], fields["locality"],
         fields["goal"], fields["interest"], fields["stage"], fields["est_value"],
         fields["next_follow_up"], _today(), fields["notes"], fields["lost_reason"],
         fields["trial_id"], db.now_iso(), db.now_iso()))
    conn.commit()
    log_update(conn, lid, "System", f"Lead created at stage “{fields['stage']}”.")
    return lid


def set_stage(conn, lid, stage, reason=None):
    """Move a lead to a new stage and log it. `reason` is used for the Lost stage."""
    if stage not in STAGES:
        return
    old = get_lead(conn, lid)
    if not old:
        return
    if stage == LOST and reason:
        conn.execute("UPDATE leads SET stage=?, lost_reason=?, updated_at=? WHERE id=?",
                     (stage, reason, db.now_iso(), lid))
    else:
        conn.execute("UPDATE leads SET stage=?, updated_at=? WHERE id=?",
                     (stage, db.now_iso(), lid))
    conn.commit()
    msg = f"Stage changed: {old['stage']} → {stage}"
    if stage == LOST and reason:
        msg += f" ({reason})"
    log_update(conn, lid, "Stage", msg)


def set_follow_up(conn, lid, day_iso, note=None):
    conn.execute("UPDATE leads SET next_follow_up=?, updated_at=? WHERE id=?",
                 (day_iso or None, db.now_iso(), lid))
    conn.commit()
    if day_iso:
        txt = f"Follow-up set for {day_iso}" + (f" — {note}" if note else "")
    else:
        txt = "Follow-up cleared"
    log_update(conn, lid, "Follow-up", txt)


# ---------------------------------------------------------------------------
# Conversion → real customer
# ---------------------------------------------------------------------------
def convert_lead(conn, lid):
    """Turn a Won-able lead into a real customer (idempotent). Returns customer id.

    Creates a customers row from the lead's details unless one is already linked,
    marks the lead Won, and records the link both ways. The caller can then send
    Balaji straight to 'book a subscription' for this customer.
    """
    lead = get_lead(conn, lid)
    if not lead:
        return None
    cid = lead["customer_id"]
    if not cid:
        cid = _next_id(conn, "customers", "C")
        conn.execute(
            "INSERT INTO customers(id,name,phone,locality,address,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (cid, lead["name"], lead["phone"], lead["locality"], None,
             f"Converted from lead {lid}." + (f" {lead['notes']}" if lead["notes"] else ""),
             db.now_iso()))
    conn.execute("UPDATE leads SET stage=?, customer_id=?, updated_at=? WHERE id=?",
                 (WON, cid, db.now_iso(), lid))
    conn.commit()
    log_update(conn, lid, "System", f"Converted → customer {cid}. 🎉")
    return cid


def link_subscription(conn, lid, sid):
    """Record which subscription was booked for a converted lead."""
    if not (lid and sid):
        return
    conn.execute("UPDATE leads SET subscription_id=?, updated_at=? WHERE id=?",
                 (sid, db.now_iso(), lid))
    conn.commit()
    log_update(conn, lid, "System", f"Subscription {sid} booked.")


# ---------------------------------------------------------------------------
# Lists / queries
# ---------------------------------------------------------------------------
def _decorate(conn, rows):
    """Add display helpers (overdue flag, trial link) without extra template logic."""
    today = _today()
    out = []
    for r in rows:
        d = dict(r)
        nf = r["next_follow_up"]
        d["due"] = bool(nf and r["stage"] in OPEN_STAGES and nf <= today)
        d["upcoming"] = bool(nf and r["stage"] in OPEN_STAGES and nf > today)
        d["is_open"] = r["stage"] in OPEN_STAGES
        out.append(d)
    return out


def list_leads(conn, stage=None, q=None):
    sql = "SELECT * FROM leads WHERE 1=1"
    args = []
    if stage == "Open":
        sql += " AND stage IN ('New','Contacted','Trial','Negotiating')"
    elif stage and stage in STAGES:
        sql += " AND stage=?"
        args.append(stage)
    if q:
        sql += " AND (name LIKE ? OR phone LIKE ? OR locality LIKE ? OR interest LIKE ?)"
        args += [f"%{q}%"] * 4
    # Open leads first; within that, soonest follow-up first; then newest.
    sql += (" ORDER BY CASE WHEN stage IN ('New','Contacted','Trial','Negotiating') THEN 0 "
            "ELSE 1 END, "
            "CASE WHEN next_follow_up IS NULL OR next_follow_up='' THEN 1 ELSE 0 END, "
            "next_follow_up ASC, id DESC")
    return _decorate(conn, conn.execute(sql, args).fetchall())


def followups_due(conn, on_or_before=None):
    """Open leads whose follow-up is today or overdue (the 'do these now' list)."""
    day = on_or_before or _today()
    rows = conn.execute(
        "SELECT * FROM leads WHERE stage IN ('New','Contacted','Trial','Negotiating') "
        "AND next_follow_up IS NOT NULL AND next_follow_up!='' AND next_follow_up<=? "
        "ORDER BY next_follow_up ASC, id ASC", (day,)).fetchall()
    return _decorate(conn, rows)


def followups_due_count(conn):
    """Lightweight count for the nav badge. Never raises if the table is missing."""
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM leads WHERE stage IN ('New','Contacted','Trial','Negotiating') "
            "AND next_follow_up IS NOT NULL AND next_follow_up!='' AND next_follow_up<=?",
            (_today(),)).fetchone()[0] or 0
    except Exception:
        return 0


def pipeline_stats(conn):
    """Headline numbers for the CRM dashboard."""
    counts = {s: 0 for s in STAGES}
    for r in conn.execute("SELECT stage, COUNT(*) n FROM leads GROUP BY stage"):
        counts[r["stage"]] = r["n"]
    total = sum(counts.values())
    open_n = sum(counts[s] for s in OPEN_STAGES)
    won = counts.get(WON, 0)
    lost = counts.get(LOST, 0)
    decided = won + lost
    conv_rate = (won / decided * 100) if decided else 0
    this_month = date.today().strftime("%b-%Y")
    won_month = conn.execute(
        "SELECT COUNT(*) n FROM leads WHERE stage='Won' "
        "AND strftime('%m-%Y', COALESCE(updated_at, created_at)) = ?",
        (date.today().strftime("%m-%Y"),)).fetchone()["n"]
    pipeline_value = conn.execute(
        "SELECT COALESCE(SUM(est_value),0) v FROM leads "
        "WHERE stage IN ('New','Contacted','Trial','Negotiating')").fetchone()["v"]
    return {
        "counts": counts, "total": total, "open": open_n, "won": won, "lost": lost,
        "conv_rate": conv_rate, "won_month": won_month, "this_month": this_month,
        "pipeline_value": pipeline_value, "due": followups_due_count(conn),
        "stage_order": STAGES, "open_stages": OPEN_STAGES,
    }


# ---------------------------------------------------------------------------
# Seed from existing trial orders (one-tap, idempotent)
# ---------------------------------------------------------------------------
def import_from_trials(conn):
    """Create a lead for each trial order that doesn't already have one.

    Trials are real prospects Balaji already cooked for, so they make a great
    starting pipeline. Re-running is safe: trials already linked to a lead (via
    leads.trial_id) are skipped. Returns the number of leads created.
    """
    linked = {r["trial_id"] for r in conn.execute(
        "SELECT trial_id FROM leads WHERE trial_id IS NOT NULL").fetchall()}
    created = 0
    for t in conn.execute("SELECT * FROM trial_orders ORDER BY date, id").fetchall():
        if t["id"] in linked:
            continue
        converted = bool((t["conversion"] or "").strip())
        stage = WON if converted else "Trial"
        lid = _next_id(conn, "leads", "L")
        conn.execute(
            """INSERT INTO leads(id, name, phone, source, locality, goal, interest, stage,
                 est_value, next_follow_up, last_contact, notes, trial_id,
                 created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, t["name"] or "Trial lead", t["phone"], "Trial", None, None,
             t["order_text"], stage, None, None, t["date"],
             t["notes"], t["id"], db.now_iso(), db.now_iso()))
        note = (f"Imported from trial {t['id']} ({t['date']})."
                + (f" Already {t['conversion']}." if converted else " Did a trial — follow up to convert."))
        log_update(conn, lid, "Trial", note, commit=False)
        created += 1
    conn.commit()
    return created
