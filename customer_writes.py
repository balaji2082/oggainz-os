"""
OG Gainz Command Center — the customer API's write half.

Build-order step 6 of `og-gainz-app/docs/03 §8`, specified by
`og-gainz-app/docs/02-skip-and-pause.md` and `docs/05-client-dashboard.md`.

Kept in its own file so `customer_api.py` stays what its docstring says it is:
read-only. This module is additive in the same way — it registers new routes and
modifies no existing view, function or template. Deleting this file returns the
Command Center to a read-only API with nothing else disturbed.

THE LAWS THIS FILE EXISTS TO OBEY
--------------------------------
[LAW] docs/03 §5.4 — writes call the existing engine, never raw SQL.
      `core.add_pause`, `core.delete_pause`, `core.resume_subscription`,
      `core.mark_delivery` already encode invariants that were each learned from
      a real bug. There is exactly one exception in this file, `skip_requests`,
      which is a new table the engine knows nothing about.

[LAW] docs/03 §5.3 — the cutoff is decided HERE, on the server, from the server's
      clock and the current setting. The browser's countdown is decoration.

[LAW] docs/02 §6.1 — a PENDING request must not change the kitchen count. Until
      Balaji approves it, the meal is still cooked and still appears on the prep
      list. Removing it early is exactly the failure of silently dropping an
      order.

[LAW] Balaji, 13 Aug 2026 — a customer's skip MARKS the order `Skipped`; it
      never deletes it, and it is no longer a one-day pause window.

      The original design used a pause window, because that is the only durable
      way to stop `ensure_day_rows` regenerating a *deleted* row. It was durable
      and it was wrong for the business: the day disappeared from the kitchen
      sheet, so nothing recorded that a skip had happened and Balaji could not
      see it where he actually works. Marking is equally durable — the generator
      skips any date that already has a row, whatever its status — and it leaves
      the X on the day, which is what he does by hand.

      A PAUSE still removes rows. That distinction is the point: a paused
      stretch should generate no orders at all, and a skipped day should appear
      and be visibly skipped.

[LAW] docs/02 §8.5 — Balaji has no cutoff. He can act for anyone on any date.

WHO IS ALLOWED TO CALL THIS
---------------------------
The same `X-OG-Service-Token` gate in `customer_api.py` covers every `/api/v1/*`
path including these. The app's server is the only holder of that token, and it
passes the `customer_id` it read from a signed session cookie — never anything
the browser sent.

The owner-only routes (`/requests`, `/settings`) are guarded by that same token
and by the app refusing to call them unless the session says `owner: true`.
Stated plainly rather than dressed up: a second token stored beside the first in
the same Vercel project would not add real security, and anyone holding the
service token can already read every customer's data.
"""

import json
from datetime import datetime, timedelta

from flask import jsonify, request

import core
import cutoff
import db
import validity

# Settings Balaji may change from the app. Deliberately a short allow-list: the
# settings table also holds API keys, and a write endpoint that takes any key is
# a write endpoint that can overwrite a credential.
WRITABLE_SETTINGS = {
    "skip_cutoff_time",
    "skip_cutoff_applies_to",
    "late_requests_allowed",
    "pause_max_days",
    "freeze_eligible_plans",
}

# The words the interface uses. Kept here so the API and the app cannot drift
# apart on the one promise that matters (docs/02 §7.4).
CARRY_FORWARD = "You don't lose the meal — it moves to the end of your pack."


def _fail(msg, code):
    return jsonify({"ok": False, "error": msg}), code


def _body():
    return request.get_json(silent=True) or {}


def _valid_date(v):
    try:
        datetime.strptime(str(v), "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _pretty(day_iso):
    """'2026-08-14' -> 'Friday 14 August' — for messages a customer reads."""
    d = datetime.strptime(day_iso, "%Y-%m-%d")
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


# ---------------------------------------------------------------------------
# Finding the pack a request is about
# ---------------------------------------------------------------------------
def _resolve_sub(conn, cid, day_iso=None, explicit=None):
    """Which subscription does this action apply to?

    In order of trustworthiness:
      1. The id the app sent, if it really belongs to this customer.
      2. The subscription behind the actual delivery row on that date.
      3. The customer's only live pack.

    A customer holding two live packs and giving no id is an ambiguity, not a
    default. Guessing would skip the wrong meal, so it is refused and the app is
    told to ask.
    """
    if explicit:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id=? AND customer_id=?",
            (str(explicit), cid)).fetchone()
        if row:
            return row, None
        return None, "that pack is not yours"

    if day_iso:
        row = conn.execute(
            "SELECT s.* FROM deliveries d JOIN subscriptions s ON s.id=d.subscription_id "
            "WHERE d.customer_id=? AND d.date=? AND d.subscription_id IS NOT NULL "
            "ORDER BY (d.meal_type='Dinner') LIMIT 1", (cid, day_iso)).fetchone()
        if row:
            return row, None

    live = conn.execute(
        "SELECT * FROM subscriptions WHERE customer_id=? AND status IN "
        "('Active','Ending Soon','Paused') ORDER BY COALESCE(start_date, booking_date) DESC",
        (cid,)).fetchall()
    if len(live) == 1:
        return live[0], None
    if not live:
        return None, "no active pack"
    return None, "several packs — say which one"


def _one_day_pause(conn, sub_id, day_iso):
    """The one-day window that represents a skip of `day_iso`, if there is one.

    Matched on start == end == the date, so a genuine multi-day freeze covering
    that date is never mistaken for a skip and silently deleted by an undo.
    """
    return conn.execute(
        "SELECT * FROM subscription_pauses WHERE subscription_id=? "
        "AND start_date=? AND end_date=?", (sub_id, day_iso, day_iso)).fetchone()


def _project_last_day(conn, sub, after_iso=None):
    """The date this pack's last meal lands on, given everything now on file.

    This is what fills in "your pack now runs to 12 Sept" — docs/02 §7.2 calls it
    the most important line on the pause screen, because it answers the question
    every customer actually has (*am I losing meals?*) before they ask it.

    Walks forward day by day through the same `plan_for_day` the generator uses,
    so the projection and the real schedule can never disagree. Bounded at a year
    so a corrupt row cannot spin.
    """
    remaining = sub["remaining_meals"] or 0
    if remaining <= 0:
        return None
    day = datetime.strptime(after_iso or cutoff.today_iso(), "%Y-%m-%d").date()
    last = None
    for _ in range(400):
        day += timedelta(days=1)
        iso = day.strftime("%Y-%m-%d")
        if not core.is_operating_day(conn, iso):
            continue
        if core.is_paused_on(conn, sub["id"], iso):
            continue
        if not core.plan_for_day(conn, sub, iso)["deliver"]:
            continue
        remaining -= 1
        last = iso
        if remaining <= 0:
            return last
    return last


# ---------------------------------------------------------------------------
# The request log — every action, applied or not
# ---------------------------------------------------------------------------
def _log(conn, cid, sub_id, kind, outcome, *, target=None, start=None, end=None,
         reason=None, deadline_for_date=None, at=None):
    """Record one action in `skip_requests` and return its id.

    [RULE] docs/03 §2.2 — a row is written for EVERY action, including ones
    applied instantly (`auto_applied`). Without that there is no history to
    screen a repeat late-asker against, which is the whole point of the queue.

    [LAW] `cutoff_at` and `minutes_late` are stored as they are at this moment
    and never recomputed later.
    """
    at = at or cutoff.now_ist()
    ref = deadline_for_date or target or start
    deadline = cutoff.deadline_for(conn, ref) if ref else at
    late = max(int((at - deadline).total_seconds() // 60), 0)
    cur = conn.execute(
        "INSERT INTO skip_requests(customer_id,subscription_id,kind,target_date,"
        "start_date,end_date,requested_at,cutoff_at,minutes_late,reason,outcome,source) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, sub_id, kind, target, start, end,
         at.strftime("%Y-%m-%d %H:%M:%S"), deadline.strftime("%Y-%m-%d %H:%M:%S"),
         late, (reason or "").strip() or None, outcome, "app"))
    conn.commit()
    return cur.lastrowid


def _request_row(conn, rid):
    return conn.execute("SELECT * FROM skip_requests WHERE id=?", (rid,)).fetchone()


def _serialise_request(r):
    return {
        "id": r["id"],
        "customer_id": r["customer_id"],
        "subscription_id": r["subscription_id"],
        "kind": r["kind"],
        "target_date": r["target_date"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "requested_at": r["requested_at"],
        "cutoff_at": r["cutoff_at"],
        "minutes_late": r["minutes_late"],
        "reason": r["reason"],
        "outcome": r["outcome"],
        "decided_at": r["decided_at"],
        "decided_by": r["decided_by"],
        "decline_reason": r["decline_reason"],
    }


# ---------------------------------------------------------------------------
# Applying, once a decision has been made (by the clock or by Balaji)
# ---------------------------------------------------------------------------
def _delivery_row(conn, sub_id, day_iso):
    """This pack's own order row on that date, whatever its status."""
    return conn.execute(
        "SELECT * FROM deliveries WHERE subscription_id=? AND date=? "
        "AND COALESCE(is_extra,0)=0 ORDER BY id LIMIT 1", (sub_id, day_iso)).fetchone()


def _apply_skip(conn, sub, day_iso, *, cooked=False):
    """Take one day off this pack, leaving the day ON the kitchen sheet.

    [LAW] Balaji's instruction, 13 Aug 2026: a customer's skip MARKS the order
    `Skipped`. It does not delete it.

    The first design made a skip a one-day pause window, because that is the
    only durable way to stop `ensure_day_rows` regenerating a deleted row. It
    worked, and it was wrong for the business: the day vanished from the day's
    order list, so nothing recorded that a skip had ever happened, and Balaji
    could not see it where he actually works. He marks skips by hand with an X;
    the app must produce the same thing, distinguishable as the customer's doing.

    Marking is durable for the same reason deleting was not: `ensure_day_rows`
    checks whether ANY row exists for the date before creating one, so a Skipped
    row suppresses regeneration exactly as a Pending one does.

    Per docs/02 §3.2 a Skipped meal still carries forward and does not consume
    the pack — `recompute_subscription` counts only Delivered against the total.
    That policy is deliberate and generous, and is unchanged here.

    `cooked` is kept for the late path: it means the food was already made when
    the skip was allowed. It changes nothing mechanically — the row is marked
    either way — but it is the honest distinction the request log records.
    """
    row = _delivery_row(conn, sub["id"], day_iso)

    if row is None:
        # A future day whose row has not been generated yet. Ask the engine to
        # make the day, rather than inventing a row here — plan_for_day decides
        # the slot and which box is cooked, and duplicating that is how the app
        # and the kitchen start disagreeing.
        core.ensure_day_rows(conn, day_iso)
        row = _delivery_row(conn, sub["id"], day_iso)

    if row is None:
        # The generator declined — the pack owes nothing more, or this weekday
        # is not one of its delivery days. There is genuinely no meal to skip,
        # and a pause window here would silently create one.
        return False

    if row["status"] != "Skipped":
        core.mark_delivery(conn, row["id"], "Skipped")
    conn.execute(
        "UPDATE deliveries SET skip_source='customer', skipped_at=? WHERE id=?",
        (db.now_iso(), row["id"]))
    conn.commit()
    return True


def _undo_skip(conn, sub, day_iso):
    """Put a skipped day back on. The exact inverse of _apply_skip.

    Also undoes a skip made under the ORIGINAL design, which wrote a one-day
    pause window instead of marking the row. Those windows exist in the live
    database — Balaji made several while testing — and without this they would
    be permanently un-undoable: the app would show the day as skipped and offer
    no way back, which is exactly what he reported on the 14th and the 17th.
    """
    undone = False

    row = _delivery_row(conn, sub["id"], day_iso)
    if row is not None and row["status"] == "Skipped":
        core.mark_delivery(conn, row["id"], "Pending")
        conn.execute(
            "UPDATE deliveries SET skip_source=NULL, skipped_at=NULL WHERE id=?", (row["id"],))
        conn.commit()
        undone = True

    legacy = _one_day_pause(conn, sub["id"], day_iso)
    if legacy is not None:
        core.delete_pause(conn, legacy["id"])
        undone = True

    return undone


def _is_skipped(conn, sub_id, day_iso):
    """Is this day off — however it was taken off?

    Covers both the current mechanism (the row marked Skipped) and the original
    one (a one-day pause window), so a day skipped last week still reads as
    skipped today and can still be undone.
    """
    row = _delivery_row(conn, sub_id, day_iso)
    if row is not None and row["status"] == "Skipped":
        return True
    return _one_day_pause(conn, sub_id, day_iso) is not None


def _apply_pause(conn, sub, start_iso, end_iso, reason):
    if core.is_paused_on(conn, sub["id"], start_iso) and \
            core.is_paused_on(conn, sub["id"], end_iso):
        return                                    # already frozen — stay idempotent
    core.add_pause(conn, sub["id"], start_iso, end_iso,
                   reason=(reason or "paused via app"))


# ---------------------------------------------------------------------------
def register(app):
    """Attach the write routes. Called from customer_api.register()."""

    # -- skip one day -------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/skip", methods=["POST"])
    def api_skip(cid):
        data = _body()
        day = str(data.get("date") or "")
        if not _valid_date(day):
            return _fail("date must be YYYY-MM-DD", 400)
        if day < cutoff.today_iso():
            return _fail("that day has already passed", 400)

        conn = db.get_db()
        try:
            sub, err = _resolve_sub(conn, cid, day, data.get("subscription_id"))
            if err:
                return _fail(err, 409)
            if not core.is_operating_day(conn, day):
                return _fail("the kitchen is closed that day — there is nothing to skip", 409)

            # Already skipped? Say so and succeed. A tap that arrived twice
            # because the connection dropped must not be treated as an error
            # (docs/02 §7.5 — every action is idempotent).
            if _is_skipped(conn, sub["id"], day):
                return jsonify({
                    "ok": True, "outcome": "applied", "already": True,
                    "applied": [day], "pending": [],
                    "message": f"{_pretty(day)} is already skipped. {CARRY_FORWARD}",
                    "pack_runs_to": _project_last_day(conn, sub),
                })

            late = cutoff.is_late(conn, day)
            if late and not cutoff.late_requests_allowed(conn):
                return _fail("it is past the deadline for that day", 409)

            outcome = "pending" if late else "auto_applied"
            rid = _log(conn, cid, sub["id"], "skip", outcome,
                       target=day, reason=data.get("reason"))

            if late:
                # [LAW] docs/02 §6.1 — nothing is touched. The meal is still
                # cooked and still on the prep list until Balaji decides.
                return jsonify({
                    "ok": True, "outcome": "requested", "request_id": rid,
                    "applied": [], "pending": [day],
                    "message": f"{_pretty(day)} is already being prepared. We've passed this on.",
                    "pack_runs_to": _project_last_day(conn, sub),
                })

            if not _apply_skip(conn, sub, day):
                return _fail("there is no meal on that day to skip", 409)
            return jsonify({
                "ok": True, "outcome": "applied", "request_id": rid,
                "applied": [day], "pending": [],
                "message": f"{_pretty(day)} skipped. {CARRY_FORWARD}",
                "pack_runs_to": _project_last_day(conn, sub),
            })
        finally:
            conn.close()

    # -- undo a skip --------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/unskip", methods=["POST"])
    def api_unskip(cid):
        """[LAW] docs/02 §2.1 — undo obeys the same deadline as the action.

        Skip, unskip, skip again, five times before 6 PM: no penalty and no
        message to Balaji. Only the state at the deadline matters. After it,
        undoing is a request too — which is what stops "I cancelled it, then
        changed my mind at 11 PM and you still didn't send food".
        """
        data = _body()
        day = str(data.get("date") or "")
        if not _valid_date(day):
            return _fail("date must be YYYY-MM-DD", 400)

        conn = db.get_db()
        try:
            sub, err = _resolve_sub(conn, cid, day, data.get("subscription_id"))
            if err:
                return _fail(err, 409)

            if not _is_skipped(conn, sub["id"], day):
                return jsonify({
                    "ok": True, "outcome": "applied", "already": True,
                    "applied": [day], "pending": [],
                    "message": f"{_pretty(day)} is already back on.",
                    "pack_runs_to": _project_last_day(conn, sub),
                })

            late = cutoff.is_late(conn, day)
            if late and not cutoff.late_requests_allowed(conn):
                return _fail("it is past the deadline for that day", 409)

            rid = _log(conn, cid, sub["id"], "undo", "pending" if late else "auto_applied",
                       target=day, reason=data.get("reason"))
            if late:
                return jsonify({
                    "ok": True, "outcome": "requested", "request_id": rid,
                    "applied": [], "pending": [day],
                    "message": f"We've passed on your request for {_pretty(day)}.",
                    "pack_runs_to": _project_last_day(conn, sub),
                })

            _undo_skip(conn, sub, day)
            return jsonify({
                "ok": True, "outcome": "applied", "request_id": rid,
                "applied": [day], "pending": [],
                "message": f"{_pretty(day)} is back on.",
                "pack_runs_to": _project_last_day(conn, sub),
            })
        finally:
            conn.close()

    # -- pause a range ------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/pause", methods=["POST"])
    def api_pause(cid):
        """Freeze a stretch of days, applying as much of it as the clock allows.

        docs/02 §4 is the whole design here: a customer at 9 PM on Thursday
        asking to pause Friday→Wednesday gets Monday, Tuesday and Wednesday
        applied immediately and only Friday waits. Rejecting all five because one
        was late is the kind of thing that makes people stop using an app.
        """
        data = _body()
        start, end = str(data.get("start") or ""), str(data.get("end") or "")
        if not _valid_date(start) or not _valid_date(end):
            return _fail("start and end must be YYYY-MM-DD", 400)
        if end < start:
            return _fail("the last day cannot be before the first", 400)
        today = cutoff.today_iso()
        if end < today:
            return _fail("that range has already passed", 400)
        start = max(start, today)

        conn = db.get_db()
        try:
            sub, err = _resolve_sub(conn, cid, start, data.get("subscription_id"))
            if err:
                return _fail(err, 409)
            if not cutoff.freeze_eligible(conn, sub["plan_name"]):
                return _fail("this pack cannot be paused", 409)

            cap = cutoff.pause_max_days(conn)
            span = (datetime.strptime(end, "%Y-%m-%d")
                    - datetime.strptime(start, "%Y-%m-%d")).days + 1
            if cap and span > cap:
                return _fail(f"a pause can run at most {cap} days", 409)

            # Only days the kitchen actually runs are meals. A Saturday inside
            # the window costs nothing and must not be counted as a lost meal.
            days = cutoff.operating_dates(conn, start, end)
            if not days:
                return _fail("no delivery days fall in those dates", 409)

            in_time, late = cutoff.split_by_deadline(conn, days)
            if late and not cutoff.late_requests_allowed(conn):
                in_time, late = in_time, []
                if not in_time:
                    return _fail("it is past the deadline for those days", 409)

            # The deadline only ever moves forward with the date, so the late
            # days are always the earliest ones and what remains is contiguous.
            applied = []
            if in_time:
                _apply_pause(conn, sub, in_time[0], end, data.get("reason"))
                applied = in_time

            rid = _log(conn, cid, sub["id"], "pause",
                       "pending" if late and not applied else
                       ("partial" if late else "auto_applied"),
                       start=start, end=end, reason=data.get("reason"),
                       deadline_for_date=days[0])

            runs_to = _project_last_day(conn, sub)
            if late and applied:
                outcome, message = "partial", (
                    f"Paused from {_pretty(applied[0])}. "
                    f"{_pretty(late[0])} is already being prepared — we've passed that on.")
            elif late:
                outcome, message = "requested", (
                    f"Those meals are already being prepared. We've passed your request on.")
            else:
                outcome, message = "applied", (
                    f"Paused {_pretty(applied[0])} to {_pretty(applied[-1])}. "
                    f"{len(applied)} meal{'s' if len(applied) != 1 else ''} moved. {CARRY_FORWARD}")

            return jsonify({
                "ok": True, "outcome": outcome, "request_id": rid,
                "applied": applied, "pending": late,
                "meals_paused": len(applied), "message": message,
                "pack_runs_to": runs_to,
            })
        finally:
            conn.close()

    # -- resume early -------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/resume", methods=["POST"])
    def api_resume(cid):
        """Start meals again from a date inside an existing pause (docs/02 §5)."""
        data = _body()
        frm = str(data.get("from") or "")
        if not _valid_date(frm):
            return _fail("from must be YYYY-MM-DD", 400)
        if frm < cutoff.today_iso():
            return _fail("that day has already passed", 400)

        conn = db.get_db()
        try:
            sub, err = _resolve_sub(conn, cid, frm, data.get("subscription_id"))
            if err:
                return _fail(err, 409)
            if not core.is_paused_on(conn, sub["id"], frm):
                return _fail("that day is not paused", 409)

            late = cutoff.is_late(conn, frm)
            if late and not cutoff.late_requests_allowed(conn):
                return _fail("it is past the deadline for that day", 409)

            rid = _log(conn, cid, sub["id"], "resume", "pending" if late else "auto_applied",
                       target=frm, reason=data.get("reason"))
            if late:
                return jsonify({
                    "ok": True, "outcome": "requested", "request_id": rid,
                    "applied": [], "pending": [frm],
                    "message": f"We've passed on your request to restart from {_pretty(frm)}.",
                })

            core.resume_subscription(conn, sub["id"], frm)
            return jsonify({
                "ok": True, "outcome": "applied", "request_id": rid,
                "applied": [frm], "pending": [],
                "message": f"Meals restart on {_pretty(frm)}.",
                "pack_runs_to": _project_last_day(conn, sub),
            })
        finally:
            conn.close()

    # -- a customer's own history ------------------------------------------
    @app.route("/api/v1/customer/<cid>/requests")
    def api_customer_requests(cid):
        conn = db.get_db()
        rows = conn.execute(
            "SELECT * FROM skip_requests WHERE customer_id=? "
            "ORDER BY requested_at DESC, id DESC LIMIT 100", (cid,)).fetchall()
        conn.close()
        return jsonify({"ok": True, "customer_id": cid,
                        "requests": [_serialise_request(r) for r in rows]})

    # -- Balaji's queue -----------------------------------------------------
    @app.route("/api/v1/activity")
    def api_activity():
        """Everything customers have done lately, decided or not.

        The queue below answers "what needs me?". This answers "what happened?",
        which is a different and equally necessary question — a skip applied
        before the deadline never enters the queue, by design, so without this
        there was no single place to see that a customer had skipped at all.
        Every action is logged (docs/03 §2.2), so this is a complete record.
        """
        limit = min(int(request.args.get("limit") or 60), 200)
        conn = db.get_db()
        rows = conn.execute(
            "SELECT r.*, c.name AS customer_name FROM skip_requests r "
            "LEFT JOIN customers c ON c.id=r.customer_id "
            "ORDER BY r.requested_at DESC, r.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            item = _serialise_request(r)
            item["customer_name"] = r["customer_name"]
            out.append(item)
        conn.close()
        return jsonify({"ok": True, "activity": out})

    @app.route("/api/v1/requests")
    def api_requests():
        """Everything waiting on a decision, with the history that informs it.

        docs/02 §8.2 — the whole purpose is that Balaji sees the pattern WHILE
        deciding, not a month later. So each pending request carries its
        customer's own record with it rather than making him go and look.

        [RULE] The badge is information, not a verdict. Nothing here auto-declines
        and nothing recommends declining. A customer might have a genuinely
        chaotic month and still be the most valuable person in the business.
        """
        conn = db.get_db()
        pending = conn.execute(
            "SELECT r.*, c.name AS customer_name FROM skip_requests r "
            "LEFT JOIN customers c ON c.id=r.customer_id "
            "WHERE r.outcome='pending' ORDER BY r.requested_at", ()).fetchall()

        month = cutoff.now_ist().strftime("%Y-%m")
        out = []
        for r in pending:
            hist = conn.execute(
                "SELECT outcome, COUNT(*) n, AVG(minutes_late) avg_late FROM skip_requests "
                "WHERE customer_id=? AND minutes_late>0 GROUP BY outcome",
                (r["customer_id"],)).fetchall()
            by = {h["outcome"]: h["n"] for h in hist}
            this_month = conn.execute(
                "SELECT COUNT(*) n FROM skip_requests WHERE customer_id=? "
                "AND minutes_late>0 AND substr(requested_at,1,7)=?",
                (r["customer_id"], month)).fetchone()["n"]
            item = _serialise_request(r)
            item["customer_name"] = r["customer_name"]
            item["history"] = {
                "late_this_month": this_month,
                "late_all_time": sum(by.values()),
                "allowed": by.get("allowed", 0),
                "declined": by.get("declined", 0),
            }
            out.append(item)
        conn.close()
        return jsonify({"ok": True, "requests": out})

    @app.route("/api/v1/requests/<int:rid>/decide", methods=["POST"])
    def api_decide(rid):
        """Allow or decline a pending request.

        [LAW] docs/02 §8.3 — a declined request leaves the meal SCHEDULED and the
        kitchen count unchanged. Nothing silently disappears.
        """
        data = _body()
        verdict = str(data.get("outcome") or "").strip()
        if verdict not in ("allowed", "declined"):
            return _fail("outcome must be allowed or declined", 400)

        conn = db.get_db()
        try:
            r = _request_row(conn, rid)
            if not r:
                return _fail("no such request", 404)
            if r["outcome"] != "pending":
                # Decided already — two taps on a slow connection, or two devices.
                return jsonify({"ok": True, "outcome": r["outcome"], "already": True})

            if verdict == "allowed":
                sub = conn.execute("SELECT * FROM subscriptions WHERE id=?",
                                   (r["subscription_id"],)).fetchone()
                if not sub:
                    return _fail("that pack no longer exists", 409)
                if r["kind"] == "skip":
                    _apply_skip(conn, sub, r["target_date"], cooked=True)
                elif r["kind"] == "pause":
                    for day in cutoff.operating_dates(conn, r["start_date"], r["end_date"]):
                        _apply_skip(conn, sub, day, cooked=True)
                elif r["kind"] == "resume":
                    core.resume_subscription(conn, sub["id"], r["target_date"])
                elif r["kind"] == "undo":
                    _undo_skip(conn, sub, r["target_date"])

            conn.execute(
                "UPDATE skip_requests SET outcome=?, decided_at=?, decided_by=?, "
                "decline_reason=? WHERE id=?",
                (verdict, db.now_iso(), str(data.get("by") or "Balaji"),
                 (data.get("reason") or "").strip() or None, rid))
            conn.commit()
            return jsonify({"ok": True, "outcome": verdict,
                            "request": _serialise_request(_request_row(conn, rid))})
        finally:
            conn.close()

    # -- the settings Balaji can change from his phone ----------------------
    @app.route("/api/v1/admin/settings", methods=["GET", "POST"])
    def api_admin_settings():
        """Read or change the deadline and its companions (docs/05 §1).

        This is the endpoint that makes the cutoff genuinely a setting rather
        than a setting in name only. Before it existed the value could only be
        changed by editing the database by hand.
        """
        conn = db.get_db()
        try:
            if request.method == "GET":
                return jsonify({"ok": True, "settings": {
                    k: db.get_setting(conn, k) for k in sorted(WRITABLE_SETTINGS)}})

            data = _body()
            changed, rejected = {}, []
            for key, value in data.items():
                if key not in WRITABLE_SETTINGS:
                    rejected.append(key)
                    continue
                value = str(value).strip()
                if key == "skip_cutoff_time":
                    try:
                        hh, mm = value.split(":")[:2]
                        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                            raise ValueError
                        value = f"{int(hh):02d}:{int(mm):02d}"
                    except (ValueError, IndexError):
                        return _fail(f"{key} must be HH:MM", 400)
                if key == "skip_cutoff_applies_to" and value not in ("calendar_day", "operating_day"):
                    return _fail("skip_cutoff_applies_to must be calendar_day or operating_day", 400)
                if key == "pause_max_days" and value:
                    if not value.isdigit():
                        return _fail("pause_max_days must be a whole number or empty", 400)
                if key == "late_requests_allowed":
                    value = "1" if value.lower() in ("1", "true", "yes", "on") else "0"
                db.set_setting(conn, key, value)
                changed[key] = value

            # [RULE] docs/02 §9 — a change takes effect on FUTURE requests only.
            # Anything already in the queue keeps the cutoff_at it was born with,
            # which is exactly why that column is stored and not recomputed.
            return jsonify({"ok": True, "changed": changed, "ignored": rejected,
                            "effective_from": cutoff.now_ist().strftime("%Y-%m-%d %H:%M:%S")})
        finally:
            conn.close()

    # -- validity: the global switch and each pack's override ---------------
    @app.route("/api/v1/admin/validity/settings", methods=["GET", "POST"])
    def api_validity_global():
        """The master switch (docs/05 §1.1 / validity.py).

        POST turns it on or off for every subscription that has never had an
        explicit per-customer decision made about it (`validity_override IS
        NULL`) — anything Balaji has already decided for one customer keeps
        that decision regardless of where this switch sits.
        """
        conn = db.get_db()
        try:
            if request.method == "GET":
                return jsonify({"ok": True, "enabled": validity.globally_enabled(conn)})
            data = _body()
            on = bool(data.get("enabled"))
            affected = validity.set_globally_enabled(conn, on, changed_by=data.get("by") or "Balaji")
            return jsonify({"ok": True, "enabled": on, "packs_affected": affected})
        finally:
            conn.close()

    @app.route("/api/v1/admin/validity")
    def api_validity_summary():
        """One row per live pack — the admin validity screen's data source.

        Deliberately separate from the ordinary Active/Ending Soon views: an
        Expired pack drops out of those (docs/01 §7.2.1 wanted it as its own
        status), so this is where Balaji actually sees it rather than it going
        quiet in a list he no longer opens for that customer.
        """
        conn = db.get_db()
        rows = validity.summary(conn)
        conn.close()
        return jsonify({"ok": True, "subscriptions": rows})

    @app.route("/api/v1/admin/subscription/<sid>/validity", methods=["POST"])
    def api_validity_override(sid):
        """Balaji's per-subscription decision — on, off, or back to following
        the global switch — and, per docs/01 §7.2.0, a direct date edit that
        needs no confirmation and no reason.
        """
        data = _body()
        override = data.get("override", "__unset__")
        if override == "__unset__":
            return _fail("override is required: 'on', 'off', or null", 400)
        if override not in ("on", "off", None):
            return _fail("override must be 'on', 'off', or null", 400)

        expires_on = data.get("expires_on")
        if expires_on and not _valid_date(expires_on):
            return _fail("expires_on must be YYYY-MM-DD", 400)

        conn = db.get_db()
        try:
            if not conn.execute("SELECT 1 FROM subscriptions WHERE id=?", (sid,)).fetchone():
                return _fail("no such subscription", 404)
            enabled = validity.set_override(
                conn, sid, override, changed_by=data.get("by") or "Balaji",
                expires_on=expires_on, note=data.get("note"))
            row = conn.execute(
                "SELECT expires_on, status FROM subscriptions WHERE id=?", (sid,)).fetchone()
            return jsonify({"ok": True, "enforced": enabled,
                            "expires_on": row["expires_on"], "status": row["status"]})
        finally:
            conn.close()

    # -- the deadline, for any set of days ----------------------------------
    @app.route("/api/v1/customer/<cid>/deadlines")
    def api_deadlines(cid):
        """Server-computed deadlines for a window of dates.

        docs/02 §7.5 — the countdown must survive the screen being locked and
        reopened, so it is computed from a deadline the SERVER supplied, never
        from a timer the browser started at page load.
        """
        frm = request.args.get("from") or cutoff.today_iso()
        to = request.args.get("to") or frm
        if not _valid_date(frm) or not _valid_date(to):
            return _fail("from/to must be YYYY-MM-DD", 400)
        conn = db.get_db()
        day = datetime.strptime(frm, "%Y-%m-%d").date()
        last = datetime.strptime(to, "%Y-%m-%d").date()
        out = {}
        while day <= last and len(out) < 120:
            iso = day.strftime("%Y-%m-%d")
            out[iso] = cutoff.describe(conn, iso)
            day += timedelta(days=1)
        conn.close()
        return jsonify({"ok": True, "server_now": cutoff.now_ist().strftime("%Y-%m-%d %H:%M:%S"),
                        "deadlines": out})
