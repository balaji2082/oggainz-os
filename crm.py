"""
OG Gainz Command Center — CRM (lead management).

The first version of this file was a place to store names. It was never used,
because storing a name is not the job. The job is: an inquiry lands on WhatsApp
while Balaji is cooking, and three weeks later that person is either paying or
forgotten. This module exists to make the second outcome hard.

So everything here is built around four questions:

    1. Can it be captured in ten seconds, one-handed, mid-shift?
    2. Does it say who to chase TODAY, without him having to remember anything?
    3. When he chases, is the message already written?
    4. Next month, can it prove where the customers came from and what they cost?

The pipeline
------------
    New -> Contacted -> Price sent -> Trial -> Won
                                          \\-> Lost

    plus two parking bays, because "Lost" was swallowing people who were never
    actually lost:

    Waitlist   they want it, they live too far to serve well right now. Held by
               area, so when four people in Tambaram are waiting, that is not a
               pile of dead leads, that is a route.
    Not now    right person, wrong month. "Call me in September" (there is a real
               trial order in the database marked exactly that).

Both resurface on their own via next_follow_up. Nothing here is ever deleted.
"""

import re
from datetime import date, datetime, timedelta

import db
import ops

# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------
WON, LOST = "Won", "Lost"
WAITLIST, NOT_NOW = "Waitlist", "Not now"

FUNNEL = ["New", "Contacted", "Price sent", "Trial", "Won"]
PARKED = [WAITLIST, NOT_NOW]
OPEN_STAGES = ["New", "Contacted", "Price sent", "Trial"]     # actively being worked
LIVE_STAGES = OPEN_STAGES + PARKED                            # not dead, just not now
STAGES = FUNNEL + [LOST] + PARKED

# `days` is the follow-up the app sets for you when a lead lands on this stage.
# It is a default and always editable — the point is that a lead can never sit
# with no next action just because Balaji was busy when he moved it.
STAGE_META = {
    "New":        {"cls": "st-new",       "days": 0,
                   "hint": "Reply today. Speed is most of the sale."},
    "Contacted":  {"cls": "st-contacted", "days": 2,
                   "hint": "You have replied — send the menu and the price next."},
    "Price sent": {"cls": "st-quoted",    "days": 2,
                   "hint": "They know the price. Offer them a trial box."},
    "Trial":      {"cls": "st-trial",     "days": 1,
                   "hint": "They ate our food. Ask the very next day — this is where packs are won."},
    WON:          {"cls": "st-won",       "days": None,
                   "hint": "Paying customer."},
    LOST:         {"cls": "st-lost",      "days": None,
                   "hint": "Closed. Record why, so it teaches us something."},
    WAITLIST:     {"cls": "st-wait",      "days": 30,
                   "hint": "Too far to serve well today. Held by area until that area clusters."},
    NOT_NOW:      {"cls": "st-later",     "days": 30,
                   "hint": "Right person, wrong month. It comes back on its own."},
}

# The first CRM shipped with a 'Negotiating' stage. Nothing was ever stored in
# it, but a database restored from an old backup could still carry it, so it is
# read as its replacement rather than crashing a template lookup.
_ALIAS = {"Negotiating": "Price sent", "Quoted": "Price sent"}

# Pickers. SOURCES and CHANNELS deliberately share their wording — a spend row
# recorded against "Meta ads" has to line up with the leads tagged "Meta ads",
# or cost per lead silently reports nothing.
SOURCES = ["WhatsApp", "Instagram", "Referral", "Walk-in", "Meta ads",
           "Google", "Existing customer", "Gym / partner", "Other"]
CHANNELS = ["Meta ads", "Instagram", "Google", "Pamphlets", "Influencer",
            "Gym / partner", "Offers", "Other"]
GOALS = ["Fat loss", "Muscle gain", "Maintenance", "General fitness", "Other"]
TEMPS = ["Hot", "Warm", "Cold"]
KINDS = {"new": "New inquiry", "trial": "Did a trial", "winback": "Past customer"}

# Fixed list, because free-text reasons cannot be counted, and the whole reason
# to record a loss is to see the same reason ten times and then go fix it.
LOST_REASONS = [
    "Too expensive",
    "Too far / out of our range",
    "Wants vegetarian only",
    "Went silent",
    "Chose someone else",
    "Just asking / not serious",
    "Timing or slot did not suit",
    "Wanted home-style, not macro food",
    "Other",
]

# Kinds of timeline entry that count as actually reaching a human. Stage moves
# and system notes deliberately do NOT, so "untouched for 9 days" stays honest
# even when a lead has been dragged around the board all week.
CONTACT_KINDS = {"WhatsApp", "Call", "Message", "Visit", "Trial"}
UPDATE_KINDS = ["WhatsApp", "Call", "Note", "Message", "Visit"]


def normalise_stage(s):
    s = (s or "").strip()
    return _ALIAS.get(s, s) or "New"


def stage_meta(stage):
    return STAGE_META.get(normalise_stage(stage), STAGE_META["New"])


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


def _plus(days):
    return (date.today() + timedelta(days=int(days or 0))).strftime("%Y-%m-%d")


def _days_between(a, b):
    """Whole days from ISO date/datetime `a` to `b`. None if either is unusable."""
    try:
        da = datetime.strptime((a or "")[:10], "%Y-%m-%d").date()
        dbb = datetime.strptime((b or "")[:10], "%Y-%m-%d").date()
        return (dbb - da).days
    except (ValueError, TypeError):
        return None


def month_label(ym):
    """'2026-08' -> 'Aug 2026'. Returns the input unchanged if it is not a month."""
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
    except (ValueError, TypeError):
        return ym or ""


def get_lead(conn, lid):
    return conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()


def updates_for(conn, lid):
    return conn.execute(
        "SELECT * FROM lead_updates WHERE lead_id=? ORDER BY at DESC, id DESC", (lid,)
    ).fetchall()


def area_read(locality, address=""):
    """What we can tell about a lead's area from free text alone.

    A lead has no map pin — nobody drops a pin before they have bought anything —
    so distance is unknowable here. What IS knowable is the corridor, from the
    same locality table the delivery routes already use. When the text matches
    nothing we say so out loud rather than guessing, because "unknown area" is
    the signal to check the distance BEFORE promising a delivery slot.
    """
    text = (locality or "").strip()
    if not text:
        return {"corridor": None, "known": False, "blank": True}
    corridor = ops.corridor_for(text, address or "")
    return {"corridor": corridor, "known": bool(corridor), "blank": False}


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------
def log_update(conn, lid, kind, text, commit=True):
    """Append a line to a lead's timeline.

    Only a real human contact moves `last_contact` and the touch counter. A
    stage change is bookkeeping, not a conversation, and counting it as one was
    how the old version could show a lead as "contacted today" when nobody had
    actually spoken to them in a fortnight.
    """
    now = db.now_iso()
    conn.execute(
        "INSERT INTO lead_updates(lead_id, at, kind, text) VALUES(?,?,?,?)",
        (lid, now, kind, text))
    if kind in CONTACT_KINDS:
        conn.execute(
            "UPDATE leads SET last_contact=?, touches=COALESCE(touches,0)+1, "
            "first_reply_at=COALESCE(first_reply_at,?), updated_at=? WHERE id=?",
            (_today(), now, now, lid))
    else:
        conn.execute("UPDATE leads SET updated_at=? WHERE id=?", (now, lid))
    if commit:
        conn.commit()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(r"(?:\+?91[\s\-]?)?([6-9]\d{9})")
_JUNK_NAME = re.compile(r"^(hi|hello|hey|vanakkam|bro|sir|madam|anna|good)\b", re.I)

# Words that say what someone is asking about, mapped to what we actually sell.
_INTEREST_HINTS = [
    ("athlete", "Athlete pack"), ("elite", "Elite pack"), ("signature", "Signature pack"),
    ("trial", "Trial box"), ("weekly", "Weekly pack"), ("monthly", "Monthly pack"),
    ("fat loss", "Fat loss"), ("weight loss", "Fat loss"), ("weight lose", "Fat loss"),
    ("muscle", "Muscle gain"), ("bulk", "Muscle gain"),
    ("diet", "Diet plan"), ("meal plan", "Meal plan"), ("lunch", "Lunch"),
    ("dinner", "Dinner"), ("coaching", "Coaching"), ("personal training", "Coaching"),
]


def parse_inquiry(text):
    """Pull a lead out of a pasted WhatsApp message. Best-effort, never wrong-loudly.

    Balaji's inquiries arrive as a screenshot's worth of text — a forwarded
    contact card, a name with a tilde, a number typed into the chat, a sentence
    about wanting to lose weight. Typing that into six form fields is exactly the
    friction that stopped him using the old CRM, so the form fills itself and he
    corrects whatever is wrong.

    Returns a dict with the same keys the lead form uses. Anything not found is
    left blank rather than invented.
    """
    raw = (text or "").strip()
    out = {"name": "", "phone": "", "locality": "", "interest": "", "notes": raw}
    if not raw:
        return out

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    blob = raw.lower()

    # Phone — the one thing worth being strict about, since it is the key we
    # de-duplicate on. Indian mobiles only. People type a number every which way
    # ("+91 98765 43210", "98765-43210", "(91) 9876543210"), so any separator
    # sitting BETWEEN two digits is closed up first; separators elsewhere are
    # left alone so a number never runs into the words around it.
    joined = re.sub(r"(?<=\d)[\s\-.()]+(?=\d)", "", raw)
    m = _PHONE_RE.search(joined)
    if m:
        out["phone"] = m.group(1)

    # Name. WhatsApp group messages prefix "~ Nickname"; a forwarded contact card
    # puts the name on its own first line; otherwise take the first line that
    # reads like a name and not like a greeting.
    for ln in lines:
        tilde = re.match(r"^~\s*(.+)$", ln)
        if tilde:
            out["name"] = tilde.group(1).strip()
            break
        label = re.match(r"^(?:name|peyar)\s*[:\-]\s*(.+)$", ln, re.I)
        if label:
            out["name"] = label.group(1).strip()
            break
    if not out["name"]:
        for ln in lines:
            candidate = _PHONE_RE.sub("", ln).strip(" -:,")
            if (candidate and len(candidate) <= 30 and not _JUNK_NAME.match(candidate)
                    and len(candidate.split()) <= 4
                    and any(ch.isalpha() for ch in candidate)):
                out["name"] = candidate
                break

    # Locality — search the same names the delivery routes already know.
    norm = ops._norm(raw)
    for key in sorted(ops._LOCALITY_CORRIDOR, key=len, reverse=True):
        if key in norm:
            out["locality"] = key.title()
            break
    label = re.search(r"^(?:area|location|place|locality)\s*[:\-]\s*(.+)$",
                      raw, re.I | re.M)
    if label:
        out["locality"] = label.group(1).strip()

    for needle, label_text in _INTEREST_HINTS:
        if needle in blob:
            out["interest"] = label_text
            break

    return out


def find_duplicates(conn, phone, exclude_lead=None):
    """Has this number reached us before? Returns existing leads and customers.

    Repeat inquiries are normal — someone asks in June, goes quiet, asks again in
    August. Creating a second lead loses the whole earlier conversation, which is
    precisely the context that closes the second one.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    if len(digits) < 10:
        return {"leads": [], "customers": []}
    like = f"%{digits}%"
    lead_sql = "SELECT * FROM leads WHERE REPLACE(REPLACE(phone,' ',''),'-','') LIKE ?"
    args = [like]
    if exclude_lead:
        lead_sql += " AND id!=?"
        args.append(exclude_lead)
    return {
        "leads": conn.execute(lead_sql + " ORDER BY id DESC", args).fetchall(),
        "customers": conn.execute(
            "SELECT * FROM customers WHERE REPLACE(REPLACE(phone,' ',''),'-','') LIKE ? "
            "ORDER BY id", (like,)).fetchall(),
    }


def save_lead(conn, f, lid=None):
    """Insert or update a lead from a form-like mapping `f`. Returns the lead id."""
    def num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def txt(key):
        return (f.get(key) or "").strip() or None

    stage = normalise_stage(f.get("stage") or "New")
    area = area_read(f.get("locality"))
    follow = (f.get("next_follow_up") or "").strip()
    if not follow and stage in LIVE_STAGES:
        follow = _plus(stage_meta(stage)["days"] or 0)

    vals = dict(
        name=(f.get("name") or "").strip() or "Unnamed lead",
        phone=(f.get("phone") or "").strip(),
        source=txt("source"), source_detail=txt("source_detail"), campaign=txt("campaign"),
        locality=txt("locality"), corridor=area["corridor"],
        goal=txt("goal"), interest=txt("interest"), temp=txt("temp"),
        stage=stage, est_value=num(f.get("est_value")),
        next_follow_up=follow or None, notes=txt("notes"),
        lost_reason=txt("lost_reason"), trial_id=txt("trial_id"),
        kind=(f.get("kind") or "new").strip() or "new",
    )

    if lid:
        old = get_lead(conn, lid)
        old_stage = normalise_stage(old["stage"]) if old else None
        conn.execute(
            """UPDATE leads SET name=?, phone=?, source=?, source_detail=?, campaign=?,
                 locality=?, corridor=?, goal=?, interest=?, temp=?, stage=?, est_value=?,
                 next_follow_up=?, notes=?, lost_reason=?, trial_id=?, kind=?, updated_at=?
               WHERE id=?""",
            (vals["name"], vals["phone"], vals["source"], vals["source_detail"],
             vals["campaign"], vals["locality"], vals["corridor"], vals["goal"],
             vals["interest"], vals["temp"], vals["stage"], vals["est_value"],
             vals["next_follow_up"], vals["notes"], vals["lost_reason"],
             vals["trial_id"], vals["kind"], db.now_iso(), lid))
        conn.commit()
        if old_stage and old_stage != vals["stage"]:
            _stamp_stage(conn, lid, vals["stage"])
            log_update(conn, lid, "Stage", f"Stage changed: {old_stage} -> {vals['stage']}")
        return lid

    lid = _next_id(conn, "leads", "L")
    peak = vals["stage"] if vals["stage"] in FUNNEL else "New"
    conn.execute(
        """INSERT INTO leads(id, name, phone, source, source_detail, campaign, locality,
             corridor, goal, interest, temp, stage, est_value, next_follow_up, last_contact,
             notes, lost_reason, trial_id, kind, stage_at, peak_stage, touches,
             created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (lid, vals["name"], vals["phone"], vals["source"], vals["source_detail"],
         vals["campaign"], vals["locality"], vals["corridor"], vals["goal"],
         vals["interest"], vals["temp"], vals["stage"], vals["est_value"],
         vals["next_follow_up"], None, vals["notes"], vals["lost_reason"],
         vals["trial_id"], vals["kind"], _today(), peak, 0, db.now_iso(), db.now_iso()))
    conn.commit()
    log_update(conn, lid, "System", f"Lead created at stage {vals['stage']}.")
    if vals["locality"] and not area["known"]:
        log_update(conn, lid, "System",
                   f"{vals['locality']} is not an area we already deliver to — "
                   "check the distance before promising a slot.")
    return lid


def quick_add(conn, name, phone, text="", source=None):
    """The ten-second capture: a name, a number, and whatever they said.

    Everything else can be filled in later, or never. A lead that exists with two
    fields beats a lead that was never entered because the form was long.
    """
    parsed = parse_inquiry(text) if text else {}
    return save_lead(conn, {
        "name": (name or "").strip() or parsed.get("name") or "Unnamed lead",
        "phone": (phone or "").strip() or parsed.get("phone") or "",
        "locality": parsed.get("locality") or "",
        "interest": parsed.get("interest") or "",
        "notes": (text or "").strip(),
        "source": source or "WhatsApp",
        "stage": "New",
    })


# ---------------------------------------------------------------------------
# Moving through the pipeline
# ---------------------------------------------------------------------------
def peak_index(row):
    """How far down the funnel this lead ever got, as an index into FUNNEL.

    Falls back to the current stage for rows written before peak_stage existed,
    and to 0 ("New") for anything parked or lost with no history — every lead has
    by definition reached New.
    """
    row = dict(row or {})
    for key in ("peak_stage", "stage"):
        s = normalise_stage(row.get(key))
        if s in FUNNEL:
            return FUNNEL.index(s)
    return 0


def _stamp_stage(conn, lid, stage):
    """Record when the CURRENT stage began, how far the lead ever got, and when
    it was decided.

    `updated_at` moves on every edit, so it could never answer "how long has this
    been sitting in Price sent?" or "how many did we win in July?".

    peak_stage only ever moves FORWARD. Moving someone back a step (a mis-tap, or
    a deal that cooled off) must not erase the fact that they once reached Trial,
    or the drop-off report quietly rewrites its own history.
    """
    now, today = db.now_iso(), _today()
    conn.execute("UPDATE leads SET stage_at=?, updated_at=? WHERE id=?", (today, now, lid))
    if stage in FUNNEL:
        row = conn.execute("SELECT peak_stage, stage FROM leads WHERE id=?", (lid,)).fetchone()
        if FUNNEL.index(stage) > (peak_index(row) if row else 0) or not (
                row and row["peak_stage"]):
            conn.execute("UPDATE leads SET peak_stage=? WHERE id=?", (stage, lid))
    if stage == WON:
        conn.execute("UPDATE leads SET won_at=COALESCE(won_at,?) WHERE id=?", (today, lid))
    elif stage == LOST:
        conn.execute("UPDATE leads SET lost_at=? WHERE id=?", (today, lid))
    conn.commit()


def set_stage(conn, lid, stage, reason=None, note=None):
    """Move a lead and log it, setting the next action automatically.

    Landing on a live stage always leaves a next_follow_up behind. A lead with no
    next action is invisible, and invisible leads are the ones that die.
    """
    stage = normalise_stage(stage)
    if stage not in STAGES:
        return
    old = get_lead(conn, lid)
    if not old:
        return
    old_stage = normalise_stage(old["stage"])

    conn.execute("UPDATE leads SET stage=?, updated_at=? WHERE id=?",
                 (stage, db.now_iso(), lid))
    if stage == LOST and reason:
        conn.execute("UPDATE leads SET lost_reason=? WHERE id=?", (reason, lid))
    if stage in LIVE_STAGES:
        days = stage_meta(stage)["days"]
        if days is not None:
            conn.execute("UPDATE leads SET next_follow_up=? WHERE id=?", (_plus(days), lid))
    else:
        # Won and Lost are finished — a nagging follow-up on them is just noise.
        conn.execute("UPDATE leads SET next_follow_up=NULL WHERE id=?", (lid,))
    conn.commit()
    _stamp_stage(conn, lid, stage)

    msg = f"Stage changed: {old_stage} -> {stage}"
    if stage == LOST and reason:
        msg += f" ({reason})"
    if note:
        msg += f" — {note}"
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


def snooze(conn, lid, days):
    """Push the next follow-up out by N days from today. The one-tap 'not yet'."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return None
    when = _plus(days)
    conn.execute("UPDATE leads SET next_follow_up=?, updated_at=? WHERE id=?",
                 (when, db.now_iso(), lid))
    conn.commit()
    human = {1: "tomorrow", 3: "in 3 days", 7: "next week", 30: "next month"}.get(
        days, f"in {days} days")
    log_update(conn, lid, "Follow-up", f"Snoozed — come back {human} ({when}).")
    return when


# ---------------------------------------------------------------------------
# Conversion -> real customer
# ---------------------------------------------------------------------------
def convert_lead(conn, lid):
    """Turn a lead into a real customer (idempotent). Returns the customer id.

    A win-back lead already IS a customer, so it links to the existing record
    rather than creating a second copy of the same person in the database.
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
        # Without an address row the new customer is invisible to the pin page and
        # the route — they could never be given a location at all.
        db.ensure_address_row(conn, cid)
    conn.execute("UPDATE leads SET stage=?, customer_id=?, next_follow_up=NULL, "
                 "updated_at=? WHERE id=?", (WON, cid, db.now_iso(), lid))
    conn.commit()
    _stamp_stage(conn, lid, WON)
    log_update(conn, lid, "System", f"Converted -> customer {cid}.")
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
    """Add the display facts a template should never have to work out itself."""
    today = _today()
    out = []
    for r in rows:
        d = dict(r)
        d["stage"] = normalise_stage(r["stage"])
        nf = r["next_follow_up"]
        live = d["stage"] in LIVE_STAGES
        d["is_open"] = d["stage"] in OPEN_STAGES
        d["is_live"] = live
        d["due"] = bool(nf and live and nf <= today)
        d["upcoming"] = bool(nf and live and nf > today)
        d["overdue_days"] = (_days_between(nf, today) or 0) if d["due"] else 0
        d["no_action"] = bool(live and not nf)
        d["quiet_days"] = _days_between(r["last_contact"] or r["created_at"], today) or 0
        d["stuck_days"] = _days_between(r["stage_at"] or r["created_at"], today) or 0
        d["meta"] = stage_meta(d["stage"])
        d["kind_label"] = KINDS.get(r["kind"] or "new", "New inquiry")
        out.append(d)
    return out


VIEWS = ["Due", "Open", "Waitlist", "Not now", "Won", "Lost", "All"]


def list_leads(conn, view="Open", q=None, source=None, kind=None):
    sql = "SELECT * FROM leads WHERE 1=1"
    args = []
    view = view or "Open"
    if view == "Open":
        sql += " AND stage IN (%s)" % ",".join("?" * len(OPEN_STAGES))
        args += OPEN_STAGES
    elif view == "Due":
        sql += (" AND stage IN (%s) AND next_follow_up IS NOT NULL AND next_follow_up!=''"
                " AND next_follow_up<=?" % ",".join("?" * len(LIVE_STAGES)))
        args += LIVE_STAGES + [_today()]
    elif view in STAGES:
        sql += " AND stage=?"
        args.append(view)
    if source:
        sql += " AND source=?"
        args.append(source)
    if kind:
        sql += " AND COALESCE(kind,'new')=?"
        args.append(kind)
    if q:
        sql += (" AND (name LIKE ? OR phone LIKE ? OR locality LIKE ? OR interest LIKE ?"
                " OR notes LIKE ?)")
        args += [f"%{q}%"] * 5
    # Anything owed today first, then the soonest next action, then newest.
    sql += (" ORDER BY CASE WHEN stage IN ('New','Contacted','Price sent','Trial') THEN 0"
            " WHEN stage IN ('Waitlist','Not now') THEN 1 ELSE 2 END,"
            " CASE WHEN next_follow_up IS NULL OR next_follow_up='' THEN 1 ELSE 0 END,"
            " next_follow_up ASC, id DESC")
    return _decorate(conn, conn.execute(sql, args).fetchall())


def followups_due(conn, on_or_before=None):
    """Everyone owed a message today or earlier — the 'do these now' list."""
    day = on_or_before or _today()
    marks = ",".join("?" * len(LIVE_STAGES))
    rows = conn.execute(
        f"SELECT * FROM leads WHERE stage IN ({marks}) "
        "AND next_follow_up IS NOT NULL AND next_follow_up!='' AND next_follow_up<=? "
        "ORDER BY next_follow_up ASC, id ASC", LIVE_STAGES + [day]).fetchall()
    return _decorate(conn, rows)


def followups_due_count(conn):
    """Lightweight count for the nav badge. Never raises if the table is missing."""
    try:
        marks = ",".join("?" * len(LIVE_STAGES))
        return conn.execute(
            f"SELECT COUNT(*) FROM leads WHERE stage IN ({marks}) "
            "AND next_follow_up IS NOT NULL AND next_follow_up!='' AND next_follow_up<=?",
            LIVE_STAGES + [_today()]).fetchone()[0] or 0
    except Exception:
        return 0


def leaking(conn, quiet_days=7):
    """Open leads that are quietly dying: no next action set, or nobody has
    spoken to them in over a week.

    This is the list a pipeline full of hope hides. It is separate from the due
    list on purpose — a lead with no follow-up date never becomes due, so it
    would otherwise never appear anywhere at all.
    """
    rows = conn.execute(
        "SELECT * FROM leads WHERE stage IN (%s)" % ",".join("?" * len(OPEN_STAGES)),
        OPEN_STAGES).fetchall()
    out = [d for d in _decorate(conn, rows)
           if d["no_action"] or (d["quiet_days"] or 0) >= quiet_days]
    out.sort(key=lambda d: -(d["quiet_days"] or 0))
    return out


def waitlist_by_area(conn):
    """Waitlisted leads grouped by area, biggest cluster first.

    Four people waiting in one place is not four dead leads, it is a reason to
    extend the route. Ungrouped, that fact is invisible.
    """
    rows = _decorate(conn, conn.execute(
        "SELECT * FROM leads WHERE stage=? ORDER BY locality, id", (WAITLIST,)).fetchall())
    groups = {}
    for d in rows:
        key = (d["locality"] or "Area not given").strip().title()
        groups.setdefault(key, {"area": key, "corridor": d.get("corridor"), "leads": []})
        groups[key]["leads"].append(d)
    out = list(groups.values())
    for g in out:
        g["n"] = len(g["leads"])
    out.sort(key=lambda g: (-g["n"], g["area"]))
    return out


def pipeline_stats(conn):
    """Headline numbers for the CRM dashboard."""
    counts = {s: 0 for s in STAGES}
    for r in conn.execute("SELECT stage, COUNT(*) n FROM leads GROUP BY stage"):
        s = normalise_stage(r["stage"])
        counts[s] = counts.get(s, 0) + r["n"]
    total = sum(counts.values())
    open_n = sum(counts[s] for s in OPEN_STAGES)
    won, lost = counts.get(WON, 0), counts.get(LOST, 0)
    decided = won + lost
    this_month = date.today().strftime("%Y-%m")
    new_month = conn.execute(
        "SELECT COUNT(*) n FROM leads WHERE substr(COALESCE(created_at,''),1,7)=?",
        (this_month,)).fetchone()["n"]
    won_month = conn.execute(
        "SELECT COUNT(*) n FROM leads WHERE stage='Won' "
        "AND substr(COALESCE(won_at, updated_at, ''),1,7)=?", (this_month,)).fetchone()["n"]
    pipeline_value = conn.execute(
        "SELECT COALESCE(SUM(est_value),0) v FROM leads WHERE stage IN (%s)"
        % ",".join("?" * len(OPEN_STAGES)), OPEN_STAGES).fetchone()["v"]
    return {
        "counts": counts, "total": total, "open": open_n, "won": won, "lost": lost,
        "conv_rate": (won / decided * 100) if decided else 0,
        "won_month": won_month, "new_month": new_month,
        "this_month": date.today().strftime("%b %Y"),
        "pipeline_value": pipeline_value or 0,
        "due": followups_due_count(conn),
        "leaking": len(leaking(conn)),
        "waitlist": counts.get(WAITLIST, 0), "not_now": counts.get(NOT_NOW, 0),
        "stage_order": STAGES, "funnel": FUNNEL, "open_stages": OPEN_STAGES,
    }


# ---------------------------------------------------------------------------
# WhatsApp message templates
# ---------------------------------------------------------------------------
# Seeded once, then his to edit. The wording follows the OG Gainz voice — "we"
# and "our", never "I" and "my", because a customer is buying from a kitchen,
# not from a bloke with a stove.
SEED_TEMPLATES = [
    ("First reply", "New",
     "Hi {first}! Thanks for reaching out to OG Gainz.\n\n"
     "We're a macro-counted meal kitchen in Perumbakkam — fresh chicken and egg "
     "based meals, weighed to your goal, delivered to your door.\n\n"
     "Quick few things so we can suggest the right pack:\n"
     "1. Which area are you in?\n"
     "2. Lunch or dinner?\n"
     "3. Fat loss, muscle gain, or just clean eating?",
     "Contacted", 2),
    ("Menu & pricing", "Contacted",
     "Here's everything — our menu, the packs and the pricing:\n{brochure}\n\n"
     "Short version:\n"
     "- Signature: Rs.3,999/month (20 meals)\n"
     "- Elite: Rs.4,999/month (20 meals)\n"
     "- Athlete: Rs.5,999/month (20 meals, 150g chicken)\n\n"
     "Weekly packs and single trial boxes are available too.\n\n"
     "Want us to send a trial box first so you can taste it?",
     "Price sent", 2),
    ("Offer a trial box", "Price sent",
     "Hi {first}, no pressure at all — the easiest way to decide is to just try one.\n\n"
     "We can send a single trial box so you can taste the food and see the portion "
     "before committing to a pack. Which day suits you, and lunch or dinner?",
     "Trial", 2),
    ("After the trial", "Trial",
     "Hi {first}! Hope you enjoyed yesterday's box.\n\n"
     "How was the taste and the portion? Anything you'd want changed — more chicken, "
     "less spice, a different carb — we can build the pack around it.\n\n"
     "Shall we start you on a pack from this week?",
     None, 2),
    ("Still interested?", None,
     "Hi {first}, just checking in from OG Gainz.\n\n"
     "Are you still looking to start your meals? Happy to hold a slot for you this "
     "week if you are — and if the timing isn't right, just tell us when to check back.",
     None, 5),
    ("Out of our range for now", None,
     "Hi {first}, thanks so much for your interest.\n\n"
     "Honestly, {locality} is a bit outside where we can deliver reliably right now, "
     "and we'd rather tell you that than send you a cold or late box.\n\n"
     "We're adding areas as we grow — can we keep your number and message you the "
     "moment we start delivering there? You'd be first on the list.",
     "Waitlist", 30),
    ("Win-back — we miss you", None,
     "Hi {first}! It's been a while since your last OG Gainz pack.\n\n"
     "A fair bit has changed — new rotation, better portions, and the delivery run is "
     "much tighter now. Would you like to come back and give us another go this month?",
     None, 7),
    ("Confirm the booking", "Trial",
     "Perfect, {first}! Here's how we confirm:\n\n"
     "Pack: {interest}\n"
     "Start date: (tell us which day)\n\n"
     "Once the payment is in we'll add you to the delivery run and send your invoice. "
     "Welcome to OG Gainz!",
     None, 1),
]


def seed_templates(conn):
    """Write the starter templates ONCE. Idempotent, and never overwrites his edits."""
    if db.get_setting(conn, "crm_templates_seeded_v1"):
        return 0
    n = 0
    for i, (name, stage, body, advance, days) in enumerate(SEED_TEMPLATES):
        if conn.execute("SELECT 1 FROM lead_templates WHERE name=?", (name,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO lead_templates(name, stage, body, advance_to, followup_days, "
            "sort, active, created_at) VALUES(?,?,?,?,?,?,1,?)",
            (name, stage, body, advance, days, (i + 1) * 10, db.now_iso()))
        n += 1
    conn.commit()
    db.set_setting(conn, "crm_templates_seeded_v1", "1")
    return n


def list_templates(conn, stage=None, active_only=True):
    """Templates, with the ones meant for this stage first."""
    sql = "SELECT * FROM lead_templates WHERE 1=1"
    if active_only:
        sql += " AND COALESCE(active,1)=1"
    sql += " ORDER BY sort, id"
    out = [dict(r) for r in conn.execute(sql).fetchall()]
    if not stage:
        return out
    stage = normalise_stage(stage)
    for d in out:
        d["preferred"] = (d["stage"] == stage)
    out.sort(key=lambda d: (not d["preferred"], d["sort"] or 0, d["id"]))
    return out


def get_template(conn, tid):
    return conn.execute("SELECT * FROM lead_templates WHERE id=?", (tid,)).fetchone()


def save_template(conn, f, tid=None):
    def i(v, default=None):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    name = (f.get("name") or "").strip() or "Untitled"
    stage = normalise_stage(f.get("stage")) if (f.get("stage") or "").strip() else None
    body = (f.get("body") or "").strip()
    advance = (normalise_stage(f.get("advance_to"))
               if (f.get("advance_to") or "").strip() else None)
    days = i(f.get("followup_days"))
    sort = i(f.get("sort"), 100)
    active = 1 if f.get("active") in ("1", "on", "true", True) else 0
    if tid:
        conn.execute(
            "UPDATE lead_templates SET name=?, stage=?, body=?, advance_to=?, "
            "followup_days=?, sort=?, active=? WHERE id=?",
            (name, stage, body, advance, days, sort, active, tid))
        conn.commit()
        return tid
    cur = conn.execute(
        "INSERT INTO lead_templates(name, stage, body, advance_to, followup_days, sort, "
        "active, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (name, stage, body, advance, days, sort, active, db.now_iso()))
    conn.commit()
    return cur.lastrowid


def delete_template(conn, tid):
    conn.execute("DELETE FROM lead_templates WHERE id=?", (tid,))
    conn.commit()


def render_body(body, lead, brochure_url=""):
    """Fill {first} {name} {interest} {locality} {goal} {brochure} for this lead."""
    lead = dict(lead or {})
    name = (lead.get("name") or "").strip()
    values = {
        "name": name or "there",
        "first": name.split(" ")[0] if name else "there",
        "interest": lead.get("interest") or "your pack",
        "locality": lead.get("locality") or "your area",
        "goal": lead.get("goal") or "your goal",
        "brochure": brochure_url or "",
    }
    out = body or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def apply_template(conn, lid, tid, sent_text=""):
    """Record that a template was sent: log it, advance the stage, set the next chase.

    Sending the message and updating the record have to be ONE action. If they are
    two, the second one does not happen — that is exactly how a CRM turns into a
    graveyard of stale stages.
    """
    tpl = get_template(conn, tid)
    if not tpl:
        return None
    log_update(conn, lid, "WhatsApp",
               f"Sent: {tpl['name']}" + (f"\n{sent_text}" if sent_text else ""))
    if tpl["advance_to"]:
        lead = get_lead(conn, lid)
        if lead and normalise_stage(lead["stage"]) != normalise_stage(tpl["advance_to"]):
            set_stage(conn, lid, tpl["advance_to"])
    if tpl["followup_days"] is not None:
        set_follow_up(conn, lid, _plus(tpl["followup_days"]))
    return dict(tpl)


# ---------------------------------------------------------------------------
# Marketing spend — the other half of "did the ads work?"
# ---------------------------------------------------------------------------
def add_spend(conn, f):
    def num(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0
    month = (f.get("month") or "").strip()[:7] or date.today().strftime("%Y-%m")
    channel = (f.get("channel") or "").strip() or "Other"
    cur = conn.execute(
        "INSERT INTO marketing_spend(month, channel, campaign, amount, note, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (month, channel, (f.get("campaign") or "").strip() or None,
         num(f.get("amount")), (f.get("note") or "").strip() or None, db.now_iso()))
    conn.commit()
    return cur.lastrowid


def delete_spend(conn, sid):
    conn.execute("DELETE FROM marketing_spend WHERE id=?", (sid,))
    conn.commit()


def list_spend(conn, month=None):
    if month:
        return conn.execute(
            "SELECT * FROM marketing_spend WHERE month=? ORDER BY channel, id", (month,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM marketing_spend ORDER BY month DESC, channel, id").fetchall()


# ---------------------------------------------------------------------------
# Reporting — where the leads came from, and what they cost
# ---------------------------------------------------------------------------
def _won_value_map(conn):
    """Real rupees per won lead, in one pass.

    Deliberately NOT est_value: that is a guess Balaji typed while he was hopeful.
    A won lead's value is the pack that was actually booked. Where the lead links
    straight to a subscription we use that; where it only links to a customer we
    take every pack that customer bought from the day the lead was created, which
    is the honest answer for a win-back who came back and then bought twice.
    """
    out = {}
    for r in conn.execute("SELECT id, customer_id, subscription_id, created_at FROM leads "
                          "WHERE stage='Won'").fetchall():
        val = 0.0
        if r["subscription_id"]:
            row = conn.execute(
                "SELECT COALESCE(total_amount,0) v FROM subscriptions WHERE id=?",
                (r["subscription_id"],)).fetchone()
            val = (row["v"] if row else 0) or 0
        elif r["customer_id"]:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_amount),0) v FROM subscriptions "
                "WHERE customer_id=? AND COALESCE(booking_date, start_date, '') >= ?",
                (r["customer_id"], (r["created_at"] or "")[:10])).fetchone()
            val = (row["v"] if row else 0) or 0
        out[r["id"]] = float(val or 0)
    return out


def report(conn, months=6):
    """Everything needed to answer the four questions that justify marketing spend:
    how many leads, from where, how many converted, and at what cost.

    Leads are counted in the month they ARRIVED; wins in the month they were WON.
    Those are different months for the same lead, which is the point — a July ad
    that converts in August is still a July lead, and the conversion rate must not
    quietly credit August for it.
    """
    today = date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(max(1, int(months))):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()

    values = _won_value_map(conn)
    leads = [dict(r) for r in conn.execute("SELECT * FROM leads").fetchall()]
    for d in leads:
        d["stage"] = normalise_stage(d["stage"])
        d["_born"] = (d.get("created_at") or "")[:7]
        d["_won"] = (d.get("won_at") or d.get("updated_at") or "")[:7]
        d["_value"] = values.get(d["id"], 0.0)

    spend_by_month, spend_by_channel = {}, {}
    for r in conn.execute("SELECT month, channel, COALESCE(SUM(amount),0) amt "
                          "FROM marketing_spend GROUP BY month, channel"):
        spend_by_month[r["month"]] = spend_by_month.get(r["month"], 0) + (r["amt"] or 0)
        spend_by_channel[r["channel"]] = spend_by_channel.get(r["channel"], 0) + (r["amt"] or 0)

    # --- month by month -----------------------------------------------------
    by_month = []
    for k in keys:
        born = [d for d in leads if d["_born"] == k]
        won = [d for d in leads if d["stage"] == WON and d["_won"] == k]
        lost = [d for d in leads if d["stage"] == LOST and (d.get("lost_at") or "")[:7] == k]
        spend = spend_by_month.get(k, 0) or 0
        revenue = sum(d["_value"] for d in won)
        by_month.append({
            "month": k, "label": month_label(k),
            "leads": len(born), "won": len(won), "lost": len(lost),
            "conv_rate": (len(won) / len(born) * 100) if born else 0,
            "revenue": revenue, "spend": spend,
            "cpl": (spend / len(born)) if (spend and born) else 0,
            "cac": (spend / len(won)) if (spend and won) else 0,
            "roas": (revenue / spend) if spend else 0,
        })

    # --- source by source (all time) ---------------------------------------
    by_source = {}

    def bucket(key):
        return by_source.setdefault(key, {"source": key, "leads": 0, "won": 0, "lost": 0,
                                          "open": 0, "revenue": 0.0, "spend": 0.0})
    for d in leads:
        s = bucket(d.get("source") or "Not recorded")
        s["leads"] += 1
        if d["stage"] == WON:
            s["won"] += 1
            s["revenue"] += d["_value"]
        elif d["stage"] == LOST:
            s["lost"] += 1
        elif d["stage"] in OPEN_STAGES:
            s["open"] += 1
    for key, amt in spend_by_channel.items():
        bucket(key)["spend"] += amt or 0
    sources = list(by_source.values())
    for s in sources:
        # Deliberately won/leads, NOT won/(won+lost). With most leads still open
        # or parked, the "of decided ones" version reported WhatsApp at 100% —
        # a number that would send him spending money on the wrong thing.
        s["conv_rate"] = (s["won"] / s["leads"] * 100) if s["leads"] else 0
        s["cpl"] = (s["spend"] / s["leads"]) if (s["spend"] and s["leads"]) else 0
        s["cac"] = (s["spend"] / s["won"]) if (s["spend"] and s["won"]) else 0
        s["roas"] = (s["revenue"] / s["spend"]) if s["spend"] else 0
    sources.sort(key=lambda s: (-s["leads"], s["source"]))

    # --- why we lose --------------------------------------------------------
    reasons = {}
    for d in leads:
        if d["stage"] == LOST:
            key = (d.get("lost_reason") or "").strip() or "Not recorded"
            reasons[key] = reasons.get(key, 0) + 1
    lost_reasons = sorted(({"reason": k, "n": v} for k, v in reasons.items()),
                          key=lambda r: -r["n"])
    lost_total = sum(r["n"] for r in lost_reasons)
    for r in lost_reasons:
        r["pct"] = (r["n"] / lost_total * 100) if lost_total else 0

    # --- how fast, and how hard ---------------------------------------------
    # Only leads whose journey we actually WATCHED. An imported trial carries the
    # trial date as both its arrival and its win, so counting it would report
    # "0 days to win" — a reconstructed date pretending to be a measurement. A
    # lead imported today and won next week does count: that part was observed.
    def observed(d):
        return (d.get("kind") or "new") == "new" or (
            (d.get("won_at") or "") > (d.get("created_at") or "")[:10])
    winners = [d for d in leads if d["stage"] == WON and observed(d)]
    spans = [g for g in (_days_between(d.get("created_at"), d.get("won_at"))
                         for d in winners)
             if g is not None and g >= 0]
    touches = [d["touches"] for d in winners if d.get("touches")]

    # --- the funnel, all time ----------------------------------------------
    # Someone sitting at Trial has by definition passed Contacted and Price sent,
    # so each step counts everyone at that stage OR beyond it. Counting only the
    # people parked on a stage would show a funnel that widens, which is nonsense.
    funnel = []
    peaks = [peak_index(d) for d in leads]
    top = len(peaks)
    for i, s in enumerate(FUNNEL):
        n = len([p for p in peaks if p >= i])
        funnel.append({"stage": s, "n": n, "pct": (n / top * 100) if top else 0,
                       "cls": STAGE_META[s]["cls"]})

    by_kind = {}
    for d in leads:
        key = d.get("kind") or "new"
        k = by_kind.setdefault(key, {"kind": key, "label": KINDS.get(key, key),
                                     "leads": 0, "won": 0, "revenue": 0.0})
        k["leads"] += 1
        if d["stage"] == WON:
            k["won"] += 1
            k["revenue"] += d["_value"]
    for k in by_kind.values():
        k["conv_rate"] = (k["won"] / k["leads"] * 100) if k["leads"] else 0

    total_won = [d for d in leads if d["stage"] == WON]
    total_spend = sum(spend_by_month.values())
    total_revenue = sum(d["_value"] for d in total_won)
    return {
        "months": by_month, "sources": sources, "lost_reasons": lost_reasons,
        "funnel": funnel, "by_kind": sorted(by_kind.values(), key=lambda k: -k["leads"]),
        "spend_rows": [dict(r) for r in list_spend(conn)],
        "avg_days_to_win": (sum(spans) / len(spans)) if spans else 0,
        "avg_touches_to_win": (sum(touches) / len(touches)) if touches else 0,
        "speed_n": len(spans),          # how many wins that average is built on
        "total_leads": len(leads), "total_won": len(total_won),
        "total_revenue": total_revenue, "total_spend": total_spend,
        "overall_roas": (total_revenue / total_spend) if total_spend else 0,
        "has_spend": bool(total_spend),
    }


# ---------------------------------------------------------------------------
# Seeding the pipeline from data the business already has
# ---------------------------------------------------------------------------
def _match_customer(conn, phone, name):
    """Find the customer a converted trial turned into. Returns an id or None.

    Trials were logged long before this CRM existed, so `converted_customer_id`
    was never filled in for any of them — the conversion is recorded as a note
    like "Converted directly" and nothing points at the person. Without a link
    the lead shows as Won with zero rupees against it, which would make every
    revenue-per-source figure wrong in the same direction.

    Phone first because it is unambiguous; then an exact name match, normalised
    for case and spacing. Nothing fuzzier than that — attaching the wrong
    customer would invent revenue, which is worse than reporting none.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    if len(digits) == 10:
        row = conn.execute(
            "SELECT id FROM customers WHERE REPLACE(REPLACE(phone,' ',''),'-','') LIKE ?",
            (f"%{digits}%",)).fetchone()
        if row:
            return row["id"]
    key = ops._norm(name)
    if not key:
        return None
    hits = [r["id"] for r in conn.execute("SELECT id, name FROM customers")
            if ops._norm(r["name"]) == key]
    return hits[0] if len(hits) == 1 else None


def import_from_trials(conn):
    """Create a lead for each trial order that does not already have one.

    Trials are the warmest prospects in the business — they have eaten our food,
    they know the portion, they know the price. Re-running is safe: trials already
    linked to a lead (via leads.trial_id) are skipped. Returns leads created.
    """
    linked = {r["trial_id"] for r in conn.execute(
        "SELECT trial_id FROM leads WHERE trial_id IS NOT NULL").fetchall()}
    created = 0
    for t in conn.execute("SELECT * FROM trial_orders ORDER BY date, id").fetchall():
        if t["id"] in linked:
            continue
        conv = (t["conversion"] or "").strip()
        converted = bool(conv) and conv.lower() not in ("none", "no", "-")
        stage = WON if converted else "Trial"
        # A trial from months ago is not a hot lead any more. Parking the old ones
        # as "Not now" keeps the follow-up list honest instead of dumping three
        # dozen overdue chases on him on day one — which is how a CRM gets ignored.
        age = _days_between(t["date"], _today())
        if not converted and (age or 0) > 30:
            stage = NOT_NOW
        lid = _next_id(conn, "leads", "L")
        area = area_read(t["address"])
        follow = None if converted else _plus(stage_meta(stage)["days"] or 0)
        # Whoever this trial turned into, so a won lead carries the money it
        # actually made instead of a zero.
        cust_id = (t["converted_customer_id"]
                   or (_match_customer(conn, t["phone"], t["name"]) if converted else None))
        # The lead ARRIVED on the day of the trial, not on the day it was
        # imported. Stamping it "today" would pile four months of history into
        # this month and report a conversion rate of nearly nothing, while the
        # months that did the work would show wins with no leads to explain them.
        born = f"{t['date']} 09:00:00" if t["date"] else db.now_iso()
        conn.execute(
            """INSERT INTO leads(id, name, phone, source, locality, corridor, goal, interest,
                 stage, est_value, next_follow_up, last_contact, notes, trial_id, customer_id,
                 kind, stage_at, peak_stage, won_at, touches, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, t["name"] or "Trial lead", t["phone"], "WhatsApp", t["address"],
             area["corridor"], None, t["order_text"], stage, None, follow, t["date"],
             t["notes"], t["id"], cust_id, "trial", t["date"],
             # Parked or not, they ate a trial box — the funnel must say so.
             WON if converted else "Trial",
             t["date"] if converted else None, 1, born, db.now_iso()))
        note = (f"Imported from trial {t['id']} ({t['date']})."
                + (f" Already marked: {conv}." if converted
                   else " Ate a trial box and was never closed."))
        log_update(conn, lid, "Trial", note, commit=False)
        created += 1
    conn.commit()
    return created


def winback_candidates(conn):
    """Past customers with nothing live: the cheapest sales in the business.

    Someone who finished a pack and never renewed has already paid once, knows the
    food and knows the routine. They cost nothing to reach. They are invisible
    today because the Customers page lists everyone and says nothing about who has
    quietly stopped eating.
    """
    linked = {r["customer_id"] for r in conn.execute(
        "SELECT customer_id FROM leads WHERE customer_id IS NOT NULL "
        "AND COALESCE(kind,'')='winback'").fetchall()}
    rows = conn.execute("""
        SELECT cu.*,
               (SELECT COUNT(*) FROM subscriptions s WHERE s.customer_id=cu.id) subs,
               (SELECT MAX(s.last_delivery_date) FROM subscriptions s
                 WHERE s.customer_id=cu.id) last_del,
               (SELECT COALESCE(SUM(s.total_amount),0) FROM subscriptions s
                 WHERE s.customer_id=cu.id) spent
        FROM customers cu
        WHERE NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.customer_id=cu.id
                          AND s.status IN ('Active','Ending Soon','Paused'))
        ORDER BY last_del DESC""").fetchall()
    today = _today()
    out = []
    for r in rows:
        if r["id"] in linked:
            continue
        d = dict(r)
        d["gone_days"] = _days_between(r["last_del"], today) if r["last_del"] else None
        d["ever_ate"] = bool(r["last_del"])
        out.append(d)
    return out


def import_winbacks(conn, only_who_ate=True):
    """Turn lapsed customers into win-back leads. Idempotent, returns leads created.

    `only_who_ate` skips people entered as customers who never actually received a
    delivery — chasing them is chasing a data-entry artefact, not a person.
    """
    created = 0
    for c in winback_candidates(conn):
        if only_who_ate and not c["ever_ate"]:
            continue
        lid = _next_id(conn, "leads", "L")
        area = area_read(c.get("locality"), c.get("address") or "")
        gone = c.get("gone_days")
        conn.execute(
            """INSERT INTO leads(id, name, phone, source, locality, corridor, interest,
                 stage, est_value, next_follow_up, last_contact, notes, customer_id, kind,
                 stage_at, peak_stage, touches, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, c["name"], c.get("phone"), "Existing customer", c.get("locality"),
             area["corridor"], "Renewal", NOT_NOW, c.get("spent") or None,
             _today(), c.get("last_del"),
             f"Past customer {c['id']} — {c.get('subs') or 0} pack(s), "
             f"Rs.{int(c.get('spent') or 0)} spent so far.",
             # They converted once before, but THIS approach starts from scratch —
             # crediting it a win up front would flatter every funnel report.
             c["id"], "winback", _today(), "New", 0, db.now_iso(), db.now_iso()))
        log_update(conn, lid, "System",
                   f"Win-back: last delivery {c['last_del']}"
                   + (f", {gone} days ago." if gone is not None else "."),
                   commit=False)
        created += 1
    conn.commit()
    return created
