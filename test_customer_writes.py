"""
Tests for the customer write API (customer_writes.py) and the deadline (cutoff.py).

    python test_customer_writes.py

Copies oggainz.db to a throwaway file first and points the app at the copy, so it
can never touch real data. Refuses to run if that fails.

The tests that matter are the ones either side of the deadline, and the one that
proves CHANGING the setting moves it — `og-gainz-app/docs/02 §10` puts that first
in the build order for a reason. A cutoff that is only correct at 18:00 is a
hardcoded constant wearing a setting's clothes.

Everything here runs against a copy. Nothing is left behind: each test that
writes cleans up after itself so the copy stays reusable within a run.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_writetest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
if os.path.exists(LIVE_DB):
    shutil.copyfile(LIVE_DB, _tmp)
os.environ["OG_DB_PATH"] = _tmp
os.environ["OG_API_TOKEN"] = "test-service-token"
os.environ.setdefault("OG_PASSWORD", "test-owner-pw")
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

sys.path.insert(0, APP_DIR)
import db                                                        # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: the app is pointed at the live database.")

import core                                                      # noqa: E402
import cutoff                                                    # noqa: E402
import app as A                                                  # noqa: E402

FAILS, N = [], 0
TOK = {"X-OG-Service-Token": "test-service-token"}


def check(label, cond, extra=""):
    global N
    N += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {extra}")
        FAILS.append(label)


A.app.config["TESTING"] = True
cl = A.app.test_client()


def post(path, payload):
    return cl.post(path, json=payload, headers=TOK)


def setting(key, value):
    c = db.get_db()
    db.set_setting(c, key, value)
    c.close()


# --- pick a real subscription to act on -------------------------------------
c = db.get_db()
row = c.execute(
    "SELECT * FROM subscriptions WHERE customer_id IS NOT NULL "
    "AND status IN ('Active','Ending Soon') AND remaining_meals > 0 "
    "ORDER BY remaining_meals DESC LIMIT 1").fetchone()
if not row:
    c.close()
    sys.exit("no active subscription with meals left in this database copy")
CID, SUB = row["customer_id"], row["id"]
ORIGINAL_CUTOFF = db.get_setting(c, "skip_cutoff_time")
ORIGINAL_MODE = db.get_setting(c, "skip_cutoff_applies_to")

# A future operating day this pack actually delivers on — the only kind of day
# any of this is meaningful for.
DAY = None
probe = cutoff.now_ist().date()
for _ in range(30):
    probe += timedelta(days=1)
    iso = probe.strftime("%Y-%m-%d")
    if core.is_operating_day(c, iso) and core.plan_for_day(c, row, iso)["deliver"] \
            and not core.is_paused_on(c, SUB, iso):
        DAY = iso
        break
c.close()
if not DAY:
    sys.exit("no upcoming delivery day found for that pack")
print(f"testing against customer {CID}, pack {SUB}, day {DAY}\n")


# ===========================================================================
print("== 1. the deadline is a setting, not a constant ==")

c = db.get_db()
setting("skip_cutoff_time", "18:00")
setting("skip_cutoff_applies_to", "calendar_day")
d18 = cutoff.deadline_for(c, DAY)
check("18:00 gives an 18:00 deadline", d18.strftime("%H:%M") == "18:00", d18)
check("deadline hangs off the previous calendar day",
      d18.date().strftime("%Y-%m-%d") ==
      (datetime.strptime(DAY, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d"), d18)

# THE test. Changing the setting must move the boundary — nothing else changes.
setting("skip_cutoff_time", "09:30")
d09 = cutoff.deadline_for(c, DAY)
check("changing the setting MOVES the deadline", d09.strftime("%H:%M") == "09:30", d09)
check("...and only the time moved", d09.date() == d18.date())

# A moment that is late under one setting and in time under the other. This is
# the whole point: same instant, same day, opposite answers.
between = datetime.combine(d18.date(), datetime.strptime("12:00", "%H:%M").time())
setting("skip_cutoff_time", "18:00")
check("noon is in time when the cutoff is 18:00", not cutoff.is_late(c, DAY, at=between))
setting("skip_cutoff_time", "09:30")
check("the same moment is late when the cutoff is 09:30", cutoff.is_late(c, DAY, at=between))

setting("skip_cutoff_time", "18:00")
one_min_before = d18 - timedelta(minutes=1)
one_min_after = d18 + timedelta(minutes=1)
check("one minute before the deadline is in time", not cutoff.is_late(c, DAY, at=one_min_before))
check("one minute after the deadline is late", cutoff.is_late(c, DAY, at=one_min_after))
check("exactly on the deadline is still in time", not cutoff.is_late(c, DAY, at=d18))
check("minutes_late is 0 before the deadline", cutoff.minutes_late(c, DAY, at=one_min_before) == 0)
check("minutes_late counts up after it",
      cutoff.minutes_late(c, DAY, at=d18 + timedelta(minutes=90)) == 90)

# A malformed setting must not take skipping offline, and must not become
# midnight — which would silently make every request late.
setting("skip_cutoff_time", "not a time")
check("a broken setting falls back to 18:00, not to midnight",
      cutoff.cutoff_time(c).strftime("%H:%M") == "18:00")
setting("skip_cutoff_time", "18:00")
c.close()


# ===========================================================================
print("\n== 2. skip before the deadline applies instantly ==")

c = db.get_db()
before = db.get_setting(c, "skip_cutoff_time")
c.close()
# Force "in time" regardless of when this test is actually run: a deadline late
# tomorrow is in the future no matter the hour.
setting("skip_cutoff_time", "23:59")

r = post(f"/api/v1/customer/{CID}/skip", {"date": DAY, "subscription_id": SUB})
j = r.get_json()
check("skip returns 200", r.status_code == 200, r.status_code)
check("outcome is 'applied'", j.get("outcome") == "applied", j)
check("the skipped day comes back in `applied`", j.get("applied") == [DAY], j)
check("the carry-forward promise is in the message",
      "moves to the end of your pack" in (j.get("message") or ""), j.get("message"))
check("it says when the pack now runs to", j.get("pack_runs_to"), j)

c = db.get_db()
check("[LAW] the day became a one-day pause window, not a deleted row",
      core.is_paused_on(c, SUB, DAY))
win = c.execute("SELECT * FROM subscription_pauses WHERE subscription_id=? AND "
                "start_date=? AND end_date=?", (SUB, DAY, DAY)).fetchone()
check("the window is marked as a skip for anyone reading the table",
      win and win["reason"] == "skip via app", win["reason"] if win else None)
check("no Pending delivery row survives inside it",
      c.execute("SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND date=? "
                "AND status='Pending'", (SUB, DAY)).fetchone()["n"] == 0)
logged = c.execute("SELECT * FROM skip_requests WHERE customer_id=? AND target_date=? "
                   "ORDER BY id DESC LIMIT 1", (CID, DAY)).fetchone()
check("[RULE] even an instant action is logged", logged is not None)
check("...as auto_applied", logged and logged["outcome"] == "auto_applied",
      logged["outcome"] if logged else None)
check("...with the deadline that applied at the time", logged and logged["cutoff_at"])
c.close()

# Idempotency: the same tap arriving twice because the connection dropped must
# not become two pause windows.
r2 = post(f"/api/v1/customer/{CID}/skip", {"date": DAY, "subscription_id": SUB})
j2 = r2.get_json()
check("skipping twice is idempotent", j2.get("outcome") == "applied" and j2.get("already"), j2)
c = db.get_db()
check("...and leaves exactly one window",
      c.execute("SELECT COUNT(*) n FROM subscription_pauses WHERE subscription_id=? "
                "AND start_date=? AND end_date=?", (SUB, DAY, DAY)).fetchone()["n"] == 1)
c.close()


print("\n== 2b. THE REGRESSION: a skipped day must be VISIBLE through the API ==")
# This is the bug that made skip look completely broken from the app on day one.
# The skip worked perfectly in the database; the read API then hid the result,
# because add_pause deletes the Pending row and the projection stepped over
# paused days. The customer tapped skip and watched the day silently vanish,
# which is indistinguishable from nothing happening.
#
# Asserting the WRITE succeeded is not enough. What the customer's screen can
# actually see afterwards is the thing that was broken, so that is what is tested.
j = cl.get(f"/api/v1/customer/{CID}/meals?from={DAY}&to={DAY}", headers=TOK).get_json()
rows = [m for m in j.get("meals", []) if m["date"] == DAY]
check("the skipped day still appears in the meals list", len(rows) > 0,
      "the day vanished entirely — this is the original bug")
check("...and it is reported as Skipped, not as an ordinary meal",
      any(m["status"] == "Skipped" for m in rows), [m.get("status") for m in rows])
check("...and it names the pack it belongs to",
      all(m.get("subscription_id") for m in rows), rows)

# The same fact, from the packs endpoint, which is what the calendar shades from.
pj = cl.get(f"/api/v1/customer/{CID}/packs", headers=TOK).get_json()
pack = [p for p in pj.get("packs", []) if p["id"] == SUB]
check("the pack lists its pause windows", pack and "pauses" in pack[0], pack[0].keys() if pack else None)
check("...including this future one-day skip, which never covers today",
      pack and any(w["start"] == DAY and w["kind"] == "skip" for w in pack[0]["pauses"]),
      pack[0].get("pauses") if pack else None)


print("\n== 2c. a pack never promises more meals than it owes ==")
# Balaji's protein shake pack was finishing on the Friday and the app kept
# showing shakes the following week. `ensure_day_rows` has always capped itself
# at remaining_meals; this projection did not, so it advertised an expected meal
# every weekday to the end of the window. Promising food that is not coming is
# worse than showing nothing — it also hides that the pack needs renewing.
j = cl.get(f"/api/v1/customer/{CID}/meals?from={cutoff.today_iso()}"
           f"&to={(cutoff.now_ist().date() + timedelta(days=120)).isoformat()}",
           headers=TOK).get_json()
by_sub = {}
for m in j.get("meals", []):
    if m.get("projected") and m.get("status") == "Expected":
        by_sub[m["subscription_id"]] = by_sub.get(m["subscription_id"], 0) + 1
c = db.get_db()
for sid, projected in by_sub.items():
    # Deliberately not named `row` — that holds the subscription under test for
    # the whole file, and shadowing it here broke §6 with a baffling KeyError.
    owed_row = c.execute("SELECT remaining_meals FROM subscriptions WHERE id=?", (sid,)).fetchone()
    owed = (owed_row["remaining_meals"] or 0) if owed_row else 0
    check(f"{sid}: {projected} expected days never exceeds {owed} owed",
          projected <= owed, f"{projected} > {owed}")
if not by_sub:
    print("  .... no forward projection in this copy to check")
c.close()


print("\n== 3. undo obeys the same deadline ==")
r = post(f"/api/v1/customer/{CID}/unskip", {"date": DAY, "subscription_id": SUB})
j = r.get_json()
check("unskip returns 200", r.status_code == 200, r.status_code)
check("outcome is 'applied'", j.get("outcome") == "applied", j)
c = db.get_db()
check("the pause window is gone", not core.is_paused_on(c, SUB, DAY))
c.close()
j2 = post(f"/api/v1/customer/{CID}/unskip", {"date": DAY, "subscription_id": SUB}).get_json()
check("unskipping twice is idempotent", j2.get("already") is True, j2)


# ===========================================================================
print("\n== 4. after the deadline nothing is touched until Balaji decides ==")

setting("skip_cutoff_time", "00:00")     # every future day is now past its deadline

c = db.get_db()
pending_before = c.execute(
    "SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND date=?",
    (SUB, DAY)).fetchone()["n"]
c.close()

r = post(f"/api/v1/customer/{CID}/skip", {"date": DAY, "subscription_id": SUB,
                                          "reason": "sudden office dinner"})
j = r.get_json()
check("a late skip is accepted, not rejected", r.status_code == 200, r.status_code)
check("outcome is 'requested'", j.get("outcome") == "requested", j)
check("the day is listed as pending", j.get("pending") == [DAY], j)
check("[deviation, docs/05 §2] the message promises nothing",
      "confirm" not in (j.get("message") or "").lower()
      and "usually fast" not in (j.get("message") or "").lower(), j.get("message"))
RID = j.get("request_id")

c = db.get_db()
check("[LAW §6.1] the meal is NOT paused while the request is pending",
      not core.is_paused_on(c, SUB, DAY))
check("[LAW §6.1] the kitchen count is unchanged",
      c.execute("SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND date=?",
                (SUB, DAY)).fetchone()["n"] == pending_before)
req = c.execute("SELECT * FROM skip_requests WHERE id=?", (RID,)).fetchone()
check("the request is pending", req and req["outcome"] == "pending")
check("minutes_late was stored", req and req["minutes_late"] > 0, req["minutes_late"] if req else None)
check("the customer's reason was kept", req and req["reason"] == "sudden office dinner")
c.close()


print("\n== 5. Balaji's queue and his decision ==")
q = cl.get("/api/v1/requests", headers=TOK).get_json()
check("the queue lists it", any(x["id"] == RID for x in q.get("requests", [])), q)
mine = [x for x in q.get("requests", []) if x["id"] == RID][0]
check("it carries the customer's name", mine.get("customer_name") is not None)
check("[§8.2] it carries their history at the point of decision",
      "late_this_month" in (mine.get("history") or {}), mine.get("history"))

d = post(f"/api/v1/requests/{RID}/decide", {"outcome": "declined", "reason": "already packed"})
check("declining returns 200", d.status_code == 200, d.status_code)
c = db.get_db()
check("[LAW §8.3] a declined request leaves the meal scheduled",
      not core.is_paused_on(c, SUB, DAY))
rq = c.execute("SELECT * FROM skip_requests WHERE id=?", (RID,)).fetchone()
check("the decline reason is recorded for the customer to see",
      rq["decline_reason"] == "already packed", rq["decline_reason"])
c.close()
again = post(f"/api/v1/requests/{RID}/decide", {"outcome": "allowed"}).get_json()
check("deciding twice does not re-apply", again.get("already") is True, again)


# ===========================================================================
print("\n== 6. a pause splits around the deadline instead of failing ==")

# Two future operating days: the first past its deadline, the second still open.
c = db.get_db()
days = []
probe = datetime.strptime(DAY, "%Y-%m-%d").date()
for _ in range(40):
    iso = probe.strftime("%Y-%m-%d")
    if core.is_operating_day(c, iso) and core.plan_for_day(c, row, iso)["deliver"]:
        days.append(iso)
    if len(days) >= 3:
        break
    probe += timedelta(days=1)
c.close()

# [§7.1 of doc 01] Only some plans may be frozen at all, and this copy's pack is
# usually a Signature — which may NOT. Test the mechanics with the gate open, and
# the gate itself immediately after, rather than conflating the two.
ORIGINAL_FREEZE = None
c = db.get_db()
ORIGINAL_FREEZE = db.get_setting(c, "freeze_eligible_plans")
PLAN = row["plan_name"]
c.close()
setting("freeze_eligible_plans", PLAN)

if len(days) >= 2:
    # A cutoff time that puts day[0] in the past but leaves the later ones open:
    # set the deadline to a moment between them.
    setting("skip_cutoff_time", "23:59")
    c = db.get_db()
    # Make day[0] late by moving its deadline behind us: 'operating_day' mode is
    # not what we want here, so instead we test the pure splitter directly.
    in_time, late = cutoff.split_by_deadline(
        c, days, at=cutoff.deadline_for(c, days[0]) + timedelta(minutes=5))
    c.close()
    check("[§4] the split keeps the later days", in_time == days[1:], (in_time, late))
    check("[§4] ...and only the earliest is late", late == days[:1], (in_time, late))

    r = post(f"/api/v1/customer/{CID}/pause",
             {"start": days[0], "end": days[-1], "subscription_id": SUB})
    j = r.get_json()
    check("pause returns 200", r.status_code == 200, r.status_code)
    check("it applied something", j.get("applied"), j)
    check("it reports how many meals moved", j.get("meals_paused", 0) > 0, j)
    check("it says when the pack now runs to", j.get("pack_runs_to"), j)
    c = db.get_db()
    check("the days really are paused", core.is_paused_on(c, SUB, days[-1]))
    # clean up
    for p in c.execute("SELECT id FROM subscription_pauses WHERE subscription_id=? "
                       "AND start_date>=?", (SUB, days[0])).fetchall():
        core.delete_pause(c, p["id"])
    c.close()
else:
    print("  .... skipped: not enough upcoming delivery days in this copy")

# The gate itself: a plan that is not on the list cannot be paused, and is told
# so plainly rather than appearing to work and quietly doing nothing.
setting("freeze_eligible_plans", "Some Other Pack")
r = post(f"/api/v1/customer/{CID}/pause",
         {"start": days[0], "end": days[-1], "subscription_id": SUB})
check("[doc 01 §7.1] a plan that may not freeze is refused", r.status_code == 409, r.status_code)
check("...with a legible reason",
      "cannot be paused" in (r.get_json().get("error") or ""), r.get_json())
# An empty setting must mean "everything is eligible", never "nothing is".
setting("freeze_eligible_plans", "")
c = db.get_db()
check("an empty eligibility list does not switch the feature off for everyone",
      cutoff.freeze_eligible(c, PLAN))
c.close()
setting("freeze_eligible_plans", PLAN)


print("\n== 7. weekends and closed days are refused, not silently accepted ==")
c = db.get_db()
closed = None
probe = cutoff.now_ist().date()
for _ in range(14):
    probe += timedelta(days=1)
    iso = probe.strftime("%Y-%m-%d")
    if not core.is_operating_day(c, iso):
        closed = iso
        break
c.close()
if closed:
    r = post(f"/api/v1/customer/{CID}/skip", {"date": closed, "subscription_id": SUB})
    check("skipping a closed day is refused with a reason", r.status_code == 409, r.status_code)
    check("...and the reason is legible",
          "kitchen is closed" in (r.get_json().get("error") or ""), r.get_json())
else:
    print("  .... skipped: kitchen appears to run every day in this copy")

print("\n== 8. a customer cannot act on someone else's pack ==")
c = db.get_db()
foreign = c.execute("SELECT id FROM subscriptions WHERE customer_id != ? LIMIT 1",
                    (CID,)).fetchone()
c.close()
if foreign:
    r = post(f"/api/v1/customer/{CID}/skip", {"date": DAY, "subscription_id": foreign["id"]})
    check("a pack belonging to someone else is refused", r.status_code == 409, r.status_code)
else:
    print("  .... skipped: only one customer in this copy")

print("\n== 9. bad input is refused before anything is written ==")
for bad in [{"date": "tomorrow"}, {"date": "2026-13-45"}, {}]:
    check(f"{bad} is refused", post(f"/api/v1/customer/{CID}/skip", bad).status_code == 400, bad)
check("a past date is refused",
      post(f"/api/v1/customer/{CID}/skip", {"date": "2020-01-01"}).status_code == 400)
check("a backwards pause range is refused",
      post(f"/api/v1/customer/{CID}/pause",
           {"start": days[-1] if len(days) > 1 else DAY, "end": DAY,
            "subscription_id": SUB}).status_code in (400, 409))


print("\n== 10. the settings endpoint, and its allow-list ==")
r = post("/api/v1/admin/settings", {"skip_cutoff_time": "19:45"})
check("a valid cutoff is accepted", r.status_code == 200, r.status_code)
c = db.get_db()
check("...and really changed", db.get_setting(c, "skip_cutoff_time") == "19:45")
check("...and the deadline moved with it",
      cutoff.deadline_for(c, DAY).strftime("%H:%M") == "19:45")
c.close()
check("a malformed time is refused",
      post("/api/v1/admin/settings", {"skip_cutoff_time": "25:99"}).status_code == 400)
check("an unpadded time is normalised",
      post("/api/v1/admin/settings", {"skip_cutoff_time": "9:5"}).get_json()
      .get("changed", {}).get("skip_cutoff_time") == "09:05")
j = post("/api/v1/admin/settings", {"OG_API_TOKEN": "stolen", "og_password": "x"}).get_json()
check("[LAW] a key outside the allow-list is ignored, not written",
      sorted(j.get("ignored", [])) == ["OG_API_TOKEN", "og_password"], j)
c = db.get_db()
check("...and the real token is untouched",
      db.get_setting(c, "OG_API_TOKEN") != "stolen")
c.close()
check("the GET reports the current values",
      cl.get("/api/v1/admin/settings", headers=TOK).get_json()
      .get("settings", {}).get("skip_cutoff_time") == "09:05")

print("\n== 11. deadlines are served to the app, not guessed by it ==")
j = cl.get(f"/api/v1/customer/{CID}/deadlines?from={DAY}&to={DAY}", headers=TOK).get_json()
check("a deadline comes back for the day", DAY in (j.get("deadlines") or {}), j)
one = (j.get("deadlines") or {}).get(DAY, {})
check("it carries an absolute deadline", one.get("deadline"), one)
check("it carries the server's own clock", j.get("server_now"), j)
check("it carries the cutoff time for the copy on screen", one.get("cutoff_time"), one)

print("\n== 12. writes still need the token ==")
for path in [f"/api/v1/customer/{CID}/skip", f"/api/v1/customer/{CID}/pause",
             "/api/v1/admin/settings", "/api/v1/requests"]:
    check(f"{path} without a token", cl.post(path, json={}).status_code == 401, path)


# --- put the settings back so the copy is reusable --------------------------
setting("skip_cutoff_time", ORIGINAL_CUTOFF or "18:00")
setting("skip_cutoff_applies_to", ORIGINAL_MODE or "calendar_day")
setting("freeze_eligible_plans", ORIGINAL_FREEZE or "Elite Meal Pack,Athlete Meal Pack")

print(f"\n{N - len(FAILS)}/{N} passed")
if FAILS:
    print("FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("all good")
