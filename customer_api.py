"""
OG Gainz Command Center — the customer API (read-only).

The seam between the Command Center and the new OG Gainz app
(`Projects/og-gainz-app`). Build-order step 2 of `og-gainz-app/docs/03 §8`.

Additive by design: this module registers new routes and touches no existing
view, template or table. Deleting this file would leave the Command Center
exactly as it was.

WHO TALKS TO WHOM
-----------------
    customer's browser  ──session cookie──>  the app's own server (Vercel)
                                                     │
                                          X-OG-Service-Token
                                                     ▼
                                            this file (Flask)

[LAW] The service token NEVER reaches a browser. Only the app's server holds it.
[LAW] Every endpoint takes an explicit customer_id and returns only that
      customer's rows. The app's server passes the id it got from the session —
      never anything the browser sent.

READ ONLY, DELIBERATELY
-----------------------
There is not one write path in *this* file, and there still isn't. Skip and pause
arrived as build-order step 6 and live in `customer_writes.py`, which this module
registers at the bottom of `register()` — kept separate precisely so this
sentence stays true and the read half remains something you can reason about
without checking whether it changes anything.

As promised, those writes call `core.add_pause()` and friends rather than writing
SQL: those functions already encode invariants that were each learned from a real
bug, and reimplementing them is how they get relearned the hard way.

The map pin (also step 6) is still not built — it needs a map component and is
not on the current list.
"""

import hmac
import json
import os
from datetime import date, datetime, timedelta

from flask import jsonify, request

import core
import db
import flexibility
import menu
import validity

# Set OG_API_TOKEN in the environment (PythonAnywhere's WSGI file) to switch the
# API on. With no token set every endpoint returns 503 — so a half-finished
# deploy is closed, not open.
API_TOKEN = os.environ.get("OG_API_TOKEN")

# Settings the customer app is allowed to read. Anything not on this list stays
# private — the settings table also holds API keys.
PUBLIC_SETTINGS = [
    "skip_cutoff_time", "skip_cutoff_applies_to", "late_requests_allowed",
    "pause_max_days",
    "delivery_promise_lunch", "delivery_promise_dinner",
    "fee_band_free_km", "fee_band_mid_km", "fee_band_far_km",
]


def _authorised():
    """Constant-time token check.

    The token comes from a HEADER, not the JSON body: bodies get written to logs
    and proxied in ways URLs and headers are not. The comparison is
    hmac.compare_digest rather than '==' because a plain comparison returns early
    on the first wrong byte, and that timing difference leaks the token one
    character at a time to anyone patient enough to measure it.
    """
    if not API_TOKEN:
        return False
    sent = request.headers.get("X-OG-Service-Token", "")
    return hmac.compare_digest(sent, API_TOKEN)


def _fail(msg, code):
    return jsonify({"ok": False, "error": msg}), code


def _iso(v):
    return str(v)[:10] if v else None


def register(app):
    """Attach the API to the Flask app (called once from app.py)."""

    @app.before_request
    def _api_gate():
        """Token auth for /api/v1/*, applied before any endpoint runs.

        These paths are in PUBLIC_ENDPOINTS so the human login gate lets them
        past — this is what actually guards them instead.
        """
        if not request.path.startswith("/api/v1/"):
            return
        if not API_TOKEN:
            return _fail("api disabled — OG_API_TOKEN is not set", 503)
        if not _authorised():
            return _fail("unauthorised", 401)
        # An /api/v1/ path that matches no route must answer in JSON like every
        # other API response. Flask has already tried to route by now, so a None
        # url_rule means there is genuinely nothing there.
        if request.url_rule is None:
            return _fail("not found", 404)

    # -- settings -----------------------------------------------------------
    @app.route("/api/v1/settings/public")
    def api_public_settings():
        """The numbers the app and the terms page must not hard-code.

        There is exactly one place that knows what time the cutoff is, and this
        is how everything else asks it.
        """
        c = db.get_db()
        out = {k: db.get_setting(c, k) for k in PUBLIC_SETTINGS}
        # Weekday numbers the kitchen runs (Mon=0), so the app can grey out days
        # it will never deliver on rather than guessing Mon-Fri forever.
        out["operating_weekdays"] = sorted(core.operating_days(c))
        c.close()
        return jsonify({"ok": True, "settings": out})

    # -- finding a customer -------------------------------------------------
    @app.route("/api/v1/customers/search")
    def api_customer_search():
        """Find a customer by anything you happen to have: id, name, phone, email.

        One box, not four. When somebody signs up in the app all Balaji has is a
        phone number and a Google email; when he is looking for a person he has
        a name. Making him choose which kind of thing he is typing is friction
        for no gain, so the query is matched against all of them.

        Phone matching ignores punctuation and country codes, so '+91 98765
        43210', '098765-43210' and '9876543210' all find the same person —
        customers write their number a dozen different ways.
        """
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"ok": False, "error": "give at least 2 characters"}), 400
        limit = min(int(request.args.get("limit", 20) or 20), 50)

        # Indian mobiles are 10 digits, but people write them with +91, a
        # leading 0, spaces or dashes. Reduce whatever was typed to the last 10
        # digits and compare that against the last 10 of what is stored, so
        # every one of those spellings finds the same person.
        digits = "".join(ch for ch in q if ch.isdigit())[-10:]
        like = f"%{q.lower()}%"

        c = db.get_db()
        rows = c.execute(
            "SELECT id, name, phone, email, locality FROM customers "
            "WHERE lower(id) = lower(?) "
            "   OR lower(name) LIKE ? "
            "   OR lower(COALESCE(email,'')) LIKE ? "
            # Strip every non-digit from the stored number before comparing, so
            # the format it was typed in never decides whether it is found.
            "   OR (? != '' AND REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone,''),"
            "        ' ',''),'-',''),'(',''),')','') LIKE ?) "
            "ORDER BY (lower(id) = lower(?)) DESC, name LIMIT ?",
            (q, like, like, digits, f"%{digits}", q, limit)).fetchall()

        out = []
        for r in rows:
            live = c.execute(
                "SELECT COUNT(*) n FROM subscriptions WHERE customer_id=? "
                "AND status IN ('Active','Ending Soon')", (r["id"],)).fetchone()["n"]
            out.append({"id": r["id"], "name": r["name"], "phone": r["phone"],
                        "email": r["email"], "locality": r["locality"],
                        "active_packs": live})
        c.close()
        return jsonify({"ok": True, "query": q, "results": out})

    # -- who they are -------------------------------------------------------
    @app.route("/api/v1/customer/<cid>")
    def api_customer(cid):
        """The customer's own details — enough to greet them by name.

        Deliberately thin: name, phone, area. Not their revenue, not their
        payment history, not their notes. The app displays this; it has no
        business knowing anything else about them.
        """
        c = db.get_db()
        row = c.execute(
            "SELECT id, name, phone, email, locality FROM customers WHERE id=?", (cid,)
        ).fetchone()
        c.close()
        if not row:
            return jsonify({"ok": False, "error": "no such customer"}), 404
        return jsonify({"ok": True, "customer": {
            "id": row["id"], "name": row["name"], "phone": row["phone"],
            "email": row["email"], "locality": row["locality"],
        }})

    # -- packs --------------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/packs")
    def api_customer_packs(cid):
        """This customer's subscriptions, newest first.

        Returns every pack, not only live ones — a customer looking at last
        month's history needs the finished ones too. `live` marks which are
        currently running, and one customer can hold several at once.
        """
        c = db.get_db()
        rows = c.execute(
            "SELECT * FROM subscriptions WHERE customer_id=? "
            "ORDER BY COALESCE(start_date, booking_date) DESC, id DESC", (cid,)).fetchall()
        packs = []
        for s in rows:
            keys = s.keys()
            comp = {"base": [], "addons": []}
            for x in menu.sub_composition(c, s["id"]):
                item = {"name": x["name"], "qty": x["qty"], "unit": x["unit"],
                        "display": menu.fmt_qty(x["qty"], x["unit"]),
                        "emoji": x["emoji"], "protein": x["protein"],
                        "note": x["note"]}
                comp["addons" if x["kind"] == "addon" else "base"].append(item)
            # Standing swaps like "paneer instead of chicken on Tue and Fri" —
            # the customer should see them, they asked for them.
            rules = [{"label": menu.rule_label(r), "weekdays": r["weekdays"]}
                     for r in menu.rules_for_sub(c, s["id"])]
            pause = core.current_pause(c, s["id"])
            # Every window, not only the one covering today. `paused` below
            # answers "is this pack frozen right now"; the calendar needs to
            # shade a freeze that starts next Tuesday as well, and a one-day
            # skip three weeks out is a window that will never cover today.
            windows = [{
                "start": _iso(p["start_date"]),
                "end": _iso(p["end_date"]),
                # A one-day window created by the app's skip button. Named so
                # the interface can say "skipped" rather than "paused" — the
                # words are not interchangeable to a customer (docs/02 §7.4).
                "kind": ("skip" if p["start_date"] == p["end_date"] else "pause"),
            } for p in core.pauses_for(c, s["id"])]
            enforced = bool(s["enforce_validity"]) if "enforce_validity" in keys else False
            # Resolved once, here, by the module that owns the rule — an explicit
            # per-pack decision over the master switch (flexibility.py).
            flexible = flexibility.effective_enabled(c, s)
            packs.append({
                "id": s["id"],
                "plan": s["plan_name"],
                "duration": s["duration"],
                "slot": s["slot"],
                "preferred_timing": s["preferred_timing"],
                "status": s["status"],
                # Expired counts as live: it still owes meals and the customer
                # needs to see that, not have the pack quietly vanish into history.
                "live": s["status"] in ("Active", "Ending Soon", "Expired"),
                "start_date": _iso(s["start_date"]),
                "meals_total": s["total_meals_scheduled"],
                "meals_delivered": s["delivered_count"],
                "meals_skipped": s["skipped_count"],
                "meals_remaining": s["remaining_meals"],
                # Only meaningful when Balaji has switched validity on for this
                # pack (validity.py). Off by default, and an unenforced pack
                # must show the customer no countdown at all.
                "expires_on": (_iso(s["expires_on"]) if enforced else None),
                "validity_enforced": enforced,
                "validity_days_left": (validity.days_left(c, s) if enforced else None),
                # [LAW] docs/03 §1.4 — the app renders, it does not decide.
                # The app used to re-derive "can this pack be paused?" from a
                # comma-separated list of plan names it was handed, which is
                # the app deciding a business rule with a copy of the data.
                # The server answers the question instead, once, per pack.
                "can_skip": flexible,
                "can_pause": flexible,
                "paused": pause is not None,
                "paused_until": _iso(pause["end_date"]) if pause else None,
                "pauses": windows,
                "composition": comp,
                "rules": rules,
            })
        c.close()
        return jsonify({"ok": True, "customer_id": cid, "packs": packs})

    # -- meals --------------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/meals")
    def api_customer_meals(cid):
        """One row per delivery day in the window (default: 14 back, 14 ahead).

        [LAW] Read-only, in the strictest sense: this does NOT call
        `deliveries_for_day()`, because that function CREATES the day's rows as a
        side effect. A customer opening their app must never cause order rows to
        be generated. Days with no row yet are reported as `projected` — the
        Command Center makes them lazily when Balaji opens that day, so a future
        week legitimately has no rows and would otherwise look empty.
        """
        try:
            frm = request.args.get("from") or (date.today() - timedelta(days=14)).isoformat()
            to = request.args.get("to") or (date.today() + timedelta(days=14)).isoformat()
            datetime.strptime(frm, "%Y-%m-%d")
            datetime.strptime(to, "%Y-%m-%d")
        except ValueError:
            return _fail("from/to must be YYYY-MM-DD", 400)

        c = db.get_db()
        rows = c.execute(
            "SELECT d.*, COALESCE(v.name, s.plan_name, d.notes) AS plan_name "
            "FROM deliveries d LEFT JOIN subscriptions s ON s.id=d.subscription_id "
            "LEFT JOIN subscription_variants v ON v.id=d.variant_id "
            "WHERE d.customer_id=? AND d.date BETWEEN ? AND ? "
            "ORDER BY d.date, (d.meal_type='Dinner')", (cid, frm, to)).fetchall()
        meals = [{
            "date": _iso(r["date"]),
            "slot": r["meal_type"],
            "status": r["status"],
            "plan": r["plan_name"],
            "note": r["day_note"],
            "subscription_id": r["subscription_id"],
            "projected": False,
        } for r in rows]

        # Fill the forward gap with expected days, clearly flagged. No meal count
        # is computed here — "remaining" always comes from the engine.
        #
        # [LAW] Balaji, 14 Aug 2026 — keyed by SUBSCRIPTION, date and slot.
        #
        # It was keyed on (date, slot) alone, which is only ever correct for a
        # customer holding one pack. Ashok holds a Protein Shake and an Athlete
        # Meal Pack and BOTH deliver at Lunch: whichever subscription this loop
        # reached first claimed the day, and the second was stepped over as
        # though it already had a meal there. His Athlete calendar showed
        # nothing at all on the 17th, 18th and 19th and then meals from the
        # 20th, because the shake had quietly taken the first three days.
        #
        # Two packs on one day is not a duplicate to be collapsed; it is a
        # customer who ordered two things. C001, C012 and C015 each hold two
        # same-slot packs and all three were losing days this way.
        have = {(m["subscription_id"], m["date"], m["slot"]) for m in meals}
        # 'Paused' belongs here too. A pack frozen right now still has days after
        # the freeze ends, and leaving it out made a paused customer's calendar
        # look permanently empty rather than paused-then-resuming.
        live = c.execute(
            "SELECT * FROM subscriptions WHERE customer_id=? "
            "AND status IN ('Active','Ending Soon','Paused')", (cid,)).fetchall()
        today = date.today().isoformat()
        for s in live:
            # Ask the schedule engine day by day rather than assuming every open
            # kitchen day is a delivery day. A Mon/Wed/Fri pack used to promise the
            # customer meals on Tuesday and Thursday too, and a day pointed at an
            # alternative box showed no name at all.
            vnames = {v["id"]: v["name"] for v in c.execute(
                "SELECT id, name FROM subscription_variants WHERE subscription_id=?",
                (s["id"],)).fetchall()}

            # [LAW] Never promise more meals than the pack still owes.
            #
            # `ensure_day_rows` has always capped itself this way; this
            # projection did not, so a pack with two meals left advertised an
            # expected meal every weekday to the end of the window. A protein
            # shake pack finishing on Friday kept showing shakes the week after,
            # which is worse than showing nothing: it promises food that is not
            # coming and it hides the fact that the pack needs renewing.
            #
            # Pending rows already on the books count against the same budget,
            # exactly as the generator counts them.
            owed = s["remaining_meals"] or 0
            planned = c.execute(
                "SELECT COUNT(*) FROM deliveries WHERE subscription_id=? AND status='Pending'",
                (s["id"],)).fetchone()[0]
            budget = max(owed - planned, 0)

            day = max(frm, today)
            end = to
            cur = datetime.strptime(day, "%Y-%m-%d").date()
            last = datetime.strptime(end, "%Y-%m-%d").date()
            while cur <= last and budget > 0:
                iso = cur.isoformat()
                p = core.plan_for_day(c, s, iso)
                slot = p["slot"] or "Lunch"
                if (p["deliver"] and core.is_operating_day(c, iso)
                        and (s["id"], iso, slot) not in have
                        and not core.is_paused_on(c, s["id"], iso)):
                    meals.append({"date": iso, "slot": slot, "status": "Expected",
                                  "plan": vnames.get(p["variant_id"]) or s["plan_name"],
                                  "note": None,
                                  "subscription_id": s["id"], "projected": True})
                    have.add((s["id"], iso, slot))
                    budget -= 1
                cur += timedelta(days=1)

        # -- days the customer has taken off ---------------------------------
        #
        # [LAW] A skipped day must be VISIBLE, not merely absent.
        #
        # This is the bug that made skip look broken from the app for its whole
        # first day of life. Skipping writes a one-day pause window, which
        # correctly deletes the Pending delivery row — so the day had no row to
        # report, and the projection above deliberately steps over paused days.
        # The skip worked perfectly in the database and the customer saw the day
        # silently disappear, which reads exactly like nothing happened.
        #
        # A day the customer removed on purpose is an event with a name. It is
        # reported as one, over the WHOLE window rather than only from today,
        # because the calendar shows history too.
        for s in live:
            cur = datetime.strptime(frm, "%Y-%m-%d").date()
            last = datetime.strptime(to, "%Y-%m-%d").date()
            while cur <= last:
                iso = cur.isoformat()
                cur += timedelta(days=1)
                if not core.is_operating_day(c, iso):
                    continue
                win = core.current_pause(c, s["id"], iso)
                if not win:
                    continue
                p = core.plan_for_day(c, s, iso)
                slot = p["slot"] or s["slot"] or "Lunch"
                if (s["id"], iso, slot) in have:
                    continue
                # One day off is a skip; a stretch is a freeze. Same mechanism
                # underneath (docs/05 §2.5), but they are different words to a
                # customer and the interface must not call a skip a pause.
                # A one-day window is a skip made under the ORIGINAL design,
                # before a skip marked the row instead. Real rows now carry
                # their own Skipped status and are returned above, so this only
                # ever fires for those legacy windows — which must keep
                # rendering as skipped so they can still be undone.
                one_day = win["start_date"] == win["end_date"]
                meals.append({
                    "date": iso, "slot": slot,
                    "status": "Skipped" if one_day else "Paused",
                    "plan": s["plan_name"], "note": None,
                    "subscription_id": s["id"], "projected": True,
                    "pause_start": _iso(win["start_date"]),
                    "pause_end": _iso(win["end_date"]),
                })
                have.add((s["id"], iso, slot))
        c.close()
        meals.sort(key=lambda m: (m["date"], m["slot"] == "Dinner"))
        return jsonify({"ok": True, "customer_id": cid, "from": frm, "to": to,
                        "meals": meals})

    # -- invoices -----------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/invoices")
    def api_customer_invoices(cid):
        c = db.get_db()
        rows = c.execute(
            "SELECT id, token, inv_date, items, discount, total, received, "
            "payment_status, subscription_id FROM invoices WHERE customer_id=? "
            "ORDER BY inv_date DESC, id DESC", (cid,)).fetchall()
        out = []
        for r in rows:
            try:
                items = json.loads(r["items"] or "[]")
            except (ValueError, TypeError):
                items = []
            out.append({
                "id": r["id"],
                # The existing public link the customer may already hold. Reused
                # rather than reinvented — those URLs are on invoices already sent.
                "url": "/i/" + r["token"] if r["token"] else None,
                "date": _iso(r["inv_date"]),
                "items": items,
                "discount": r["discount"], "total": r["total"],
                "received": r["received"], "payment_status": r["payment_status"],
                "subscription_id": r["subscription_id"],
            })
        c.close()
        return jsonify({"ok": True, "customer_id": cid, "invoices": out})

    # -- addresses ----------------------------------------------------------
    @app.route("/api/v1/customer/<cid>/addresses")
    def api_customer_addresses(cid):
        """Delivery addresses, each with its own pin and weekday schedule.

        `pin_source` says who put the current pin there. When a customer's own
        pin and an admin correction disagree, the admin pin is authoritative
        until the customer edits it again — so the app has to be able to tell
        which one it is looking at.
        """
        c = db.get_db()
        rows = c.execute(
            "SELECT * FROM customer_addresses WHERE customer_id=? AND active=1 "
            "ORDER BY is_default DESC, sort, id", (cid,)).fetchall()
        out = []
        for r in rows:
            keys = r.keys()
            out.append({
                "id": r["id"], "label": r["label"], "address": r["address"],
                "lat": r["lat"], "lng": r["lng"],
                "pinned": r["lat"] is not None and r["lng"] is not None,
                "pin_source": r["pin_source"] if "pin_source" in keys else None,
                "pin_set_at": r["pin_set_at"] if "pin_set_at" in keys else None,
                "map_note": r["map_note"], "drop_type": r["drop_type"],
                "slot": r["slot"], "weekdays": r["weekdays"],
                "is_default": bool(r["is_default"]),
                # Deliberately NOT returned: zone and corridor. They are routing
                # decisions and delivery-fee inputs, not the customer's business.
            })
        c.close()
        return jsonify({"ok": True, "customer_id": cid, "addresses": out})

    # -- the write half -----------------------------------------------------
    # Registered here rather than from app.py so that adding skip and pause
    # touched exactly one existing file, by two lines. Everything they do lives
    # in customer_writes.py and is guarded by the same token gate above.
    import customer_writes
    customer_writes.register(app)
