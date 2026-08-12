"""
Tests for per-subscription delivery schedules, alternative boxes, extra meals
and pack renewal.

    python test_schedule.py

Copies oggainz.db to a throwaway file first and points the app at the copy, so it
can never touch real data. Refuses to run if that fails.

The checks that matter are the ones about NOT breaking things: that a pack with
no schedule set still behaves exactly as it always has, that the automatic
generator never creates or deletes a meal added by hand, that a schedule change
never rewrites a delivered meal, and that a renewal's boxes belong to the
renewal and not to the pack it was copied from.
"""

import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_schedtest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
if os.path.exists(LIVE_DB):
    shutil.copyfile(LIVE_DB, _tmp)
os.environ["OG_DB_PATH"] = _tmp
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

sys.path.insert(0, APP_DIR)
import db                                                        # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: the app is pointed at the live database.")

import app as A                                                  # noqa: E402
import core                                                      # noqa: E402
import menu                                                      # noqa: E402

FAILS, N = [], 0


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
c = db.get_db()

sub = c.execute("SELECT * FROM subscriptions WHERE status IN ('Active','Ending Soon') "
                "AND COALESCE(remaining_meals,0) > 3 ORDER BY id LIMIT 1").fetchone()
if not sub:
    sys.exit("no active subscription in this database copy to test against")
SID = sub["id"]
print(f"testing against subscription {SID} ({sub['customer_name']})\n")

# A Monday far enough ahead that no order rows exist for it yet.
_d = date.today() + timedelta(days=14)
MONDAY = _d - timedelta(days=_d.weekday())
WEEK = [(MONDAY + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
MON, TUE, WED, THU, FRI = WEEK
for _day in WEEK:                       # make sure the kitchen is open all week
    core.set_day_override(c, _day, 1, "test")


def counts(sid):
    r = c.execute("SELECT delivered_count,skipped_count,remaining_meals FROM subscriptions "
                  "WHERE id=?", (sid,)).fetchone()
    return (r["delivered_count"], r["skipped_count"], r["remaining_meals"])


def orders_on(sid, day):
    return c.execute("SELECT COUNT(*) FROM deliveries WHERE subscription_id=? AND date=?",
                     (sid, day)).fetchone()[0]


print("== 1. nothing changes for a pack that was never given a schedule ==")
before = {r["id"]: (r["delivered_count"], r["skipped_count"], r["remaining_meals"], r["status"])
          for r in c.execute("SELECT * FROM subscriptions")}
core.recompute_all(c)
after = {r["id"]: (r["delivered_count"], r["skipped_count"], r["remaining_meals"], r["status"])
         for r in c.execute("SELECT * FROM subscriptions")}
check("recompute moves no existing count", before == after,
      f"{[k for k in before if before[k] != after[k]][:5]}")
# Not "everything is daily" — by now some packs may deliberately be on a weekly or
# on-demand schedule. What must hold is that the column never holds anything the
# generator doesn't understand, and that a pack with no plan of its own still runs
# every kitchen day exactly as it did before any of this shipped.
check("no pack has an unreadable schedule mode",
      all(core.schedule_mode(r) in ("daily", "weekly", "ondemand")
          for r in c.execute("SELECT * FROM subscriptions")))
unplanned = [r for r in c.execute("SELECT * FROM subscriptions")
             if not core.day_plan_for(c, r["id"]) and core.schedule_mode(r) == "daily"]
check("a pack with no schedule of its own is still daily", len(unplanned) > 0)
check("...and delivers on every kitchen day",
      all(core.plan_for_day(c, r, MON)["deliver"] for r in unplanned[:20]))
check("the pack under test starts out daily", core.schedule_mode(sub) == "daily")
check("a daily pack delivers on a normal weekday",
      core.plan_for_day(c, sub, MON)["deliver"] is True)

print("\n== 2. chosen weekdays (Mon/Wed/Fri) ==")
cl.post(f"/subscriptions/{SID}/schedule", data={
    "schedule_mode": "weekly", "day_active_0": "1", "day_active_2": "1", "day_active_4": "1"})
s = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("mode saved as weekly", core.schedule_mode(s) == "weekly")
check("summary reads 'Mon, Wed, Fri'", core.schedule_summary(c, s) == "Mon, Wed, Fri")
for day, want in [(MON, True), (TUE, False), (WED, True), (THU, False), (FRI, True)]:
    core.ensure_day_rows(c, day)
    check(f"{datetime.strptime(day, '%Y-%m-%d').strftime('%a')}: "
          f"{'an order' if want else 'no order'}",
          (orders_on(SID, day) > 0) == want)

print("\n== 3. a different box on some days ==")
vid = menu.add_variant(c, SID, "Test Salad", plan_id=None)
c.execute("DELETE FROM subscription_components WHERE subscription_id=? AND variant_id=?",
          (SID, vid))
c.execute("INSERT INTO subscription_components(subscription_id,component_id,qty,kind,variant_id) "
          "VALUES(?,?,?,?,?)", (SID, 15, 1, "base", vid))
c.commit()
cl.post(f"/subscriptions/{SID}/schedule", data={
    "schedule_mode": "weekly", "day_active_0": "1",
    "day_active_2": "1", "day_variant_2": str(vid), "day_active_4": "1"})
s = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("Wed is stamped with the variant", core.plan_for_day(c, s, WED)["variant_id"] == vid)
check("Mon is still the usual box", core.plan_for_day(c, s, MON)["variant_id"] is None)
core.ensure_day_rows(c, WED)
row = c.execute("SELECT * FROM deliveries d LEFT JOIN subscriptions s ON s.id=d.subscription_id "
                "WHERE d.subscription_id=? AND d.date=?", (SID, WED)).fetchone()
items, label = menu.order_items(c, row, WED)
check("the kitchen cooks the variant on Wed", label == "Test Salad")
check("...with the variant's own contents",
      [i["component_id"] for i in items] == [15], str(items))
core.ensure_day_rows(c, MON)
row = c.execute("SELECT * FROM deliveries d LEFT JOIN subscriptions s ON s.id=d.subscription_id "
                "WHERE d.subscription_id=? AND d.date=?", (SID, MON)).fetchone()
_, label = menu.order_items(c, row, MON)
check("...and the usual box on Mon", label != "Test Salad")
check("editing the variant did not touch the normal box",
      len(menu.sub_composition(c, SID)) > 0)

# The kitchen cooking the right food is only half of it. Every screen that names
# the day's meal has to agree with the prep list, or the order reads "Signature
# Meal Pack" while the prep list beside it says salad -- which is what happened.
print("\n== 3b. the day's lists call the box by its own name ==")
wrow = next((r for r in core.deliveries_for_day(c, WED) if r["subscription_id"] == SID), None)
check("the Wed order is in the day list", wrow is not None)
check("...and is named after the variant", wrow and wrow["plan_name"] == "Test Salad",
      wrow and wrow["plan_name"])
check("...while the pack it came from is still readable",
      wrow and wrow["pack_name"] == sub["plan_name"], wrow and wrow["pack_name"])
mrow = next((r for r in core.deliveries_for_day(c, MON) if r["subscription_id"] == SID), None)
check("a normal day still shows the pack name",
      mrow and mrow["plan_name"] == sub["plan_name"], mrow and mrow["plan_name"])
check("...and reports no variant", mrow and mrow["variant_name"] is None)

# The kitchen count is keyed on that same name, so a salad must not be tallied
# into the Signature pile.
cooked = {p: n for p, m, n in core.today_summary(c, WED)["cook"]}
check("the cook list counts the salad separately", cooked.get("Test Salad") == 1, str(cooked))

_after_wed = (datetime.strptime(WED, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
brow = next((r for g in menu.backlog(c, _after_wed)["groups"] for r in g["rows"]
             if r["subscription_id"] == SID and r["date"] == WED), None)
check("the backlog names it the same way", brow and brow["plan_label"] == "Test Salad",
      brow and brow["plan_label"])

print("\n== 4. an extra meal comes out of the pack ==")
d0, sk0, rem0 = counts(SID)
did = core.add_extra_meal(c, SID, MON, slot="Dinner", status="Delivered",
                          note="test extra")
d1, sk1, rem1 = counts(SID)
check("delivered count goes up by one", d1 == d0 + 1, f"{d0} -> {d1}")
check("meals left goes down by one", rem1 == rem0 - 1, f"{rem0} -> {rem1}")
check("both meals exist on that day", orders_on(SID, MON) == 2)
core.ensure_day_rows(c, MON)
check("the generator does not duplicate around it", orders_on(SID, MON) == 2)
core.purge_unworked_day(c, MON)
check("closing the day leaves the extra alone",
      c.execute("SELECT COUNT(*) FROM deliveries WHERE id=?", (did,)).fetchone()[0] == 1)
core.recompute_subscription(c, SID)
check("recompute leaves the extra alone",
      c.execute("SELECT COUNT(*) FROM deliveries WHERE id=?", (did,)).fetchone()[0] == 1)
d2, _, rem2 = counts(SID)
check("...and the count stays corrected", (d2, rem2) == (d1, rem1))

did2 = core.add_extra_meal(c, SID, TUE, slot="Dinner", status="Delivered",
                           counts_against_pack=False, note="sold separately")
d3, _, rem3 = counts(SID)
check("a separately-sold meal does NOT use a pack meal", (d3, rem3) == (d2, rem2))
core.delete_delivery(c, did2)
core.delete_delivery(c, did)
d4, _, rem4 = counts(SID)
check("removing an extra gives the serving back", (d4, rem4) == (d0, rem0))

print("\n== 5. on demand: nothing is planned until the week is ticked ==")
cl.post(f"/subscriptions/{SID}/schedule", data={"schedule_mode": "ondemand"})
s = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
for day in WEEK:
    core.ensure_day_rows(c, day)
check("no orders are generated", sum(orders_on(SID, d) for d in WEEK) == 0)
added, removed = core.plan_week(c, SID, [TUE, THU], WEEK)
check("ticking two days plans two meals", (added, removed) == (2, 0))
check("...and only those days", [d for d in WEEK if orders_on(SID, d)] == [TUE, THU])
added, removed = core.plan_week(c, SID, [TUE], WEEK)
check("unticking a day removes it", (added, removed) == (0, 1))
check("...leaving the other", [d for d in WEEK if orders_on(SID, d)] == [TUE])
core.plan_week(c, SID, [], WEEK)

print("\n== 6. history is never rewritten by a schedule change ==")
core.plan_week(c, SID, [MON], WEEK)
row = c.execute("SELECT id FROM deliveries WHERE subscription_id=? AND date=?",
                (SID, MON)).fetchone()
core.mark_delivery(c, row["id"], "Delivered")
d_before = counts(SID)
cl.post(f"/subscriptions/{SID}/schedule", data={"schedule_mode": "weekly", "day_active_4": "1"})
check("a delivered meal survives a schedule change",
      c.execute("SELECT status FROM deliveries WHERE id=?",
                (row["id"],)).fetchone()["status"] == "Delivered")
check("...and the counts are unchanged", counts(SID) == d_before)
core.mark_delivery(c, row["id"], "Pending")
c.execute("DELETE FROM deliveries WHERE id=?", (row["id"],))
c.commit()
core.recompute_subscription(c, SID)

print("\n== 7. renewing carries the whole pack across ==")
cl.post(f"/subscriptions/{SID}/schedule", data={
    "schedule_mode": "weekly", "day_active_0": "1",
    "day_active_2": "1", "day_variant_2": str(vid)})
old_comp = [(x["component_id"], x["qty"]) for x in menu.sub_composition(c, SID)]
old_vids = {v["id"] for v in menu.variants_for_sub(c, SID)}
r = cl.get(f"/subscriptions/new?renew={SID}")
check("the renew button opens a prefilled form", r.status_code == 200)
check("...naming the pack being renewed", SID in r.get_data(as_text=True))
r = cl.post("/subscriptions/new", data={
    "customer_id": sub["customer_id"], "plan_name": sub["plan_name"],
    "duration": "Monthly", "total_meals_scheduled": "20", "slot": sub["slot"],
    "booking_date": date.today().strftime("%Y-%m-%d"),
    "start_date": date.today().strftime("%Y-%m-%d"),
    "subscription_price": "3999", "delivery_fee": "0", "amount_received": "0",
    "renew_of": SID, "schedule_mode": "weekly",
    "day_active_0": "1", "day_active_2": "1", "day_variant_2": str(vid)})
NEW = r.headers["Location"].rstrip("/").split("/")[-1]
new_comp = [(x["component_id"], x["qty"]) for x in menu.sub_composition(c, NEW)]
new_vs = {v["id"]: v["name"] for v in menu.variants_for_sub(c, NEW)}
check("the box contents came across", new_comp == old_comp, f"{old_comp} vs {new_comp}")
check("the alternative boxes came across", "Test Salad" in new_vs.values())
plan = core.day_plan_for(c, NEW)
check("the weekly schedule came across", sorted(plan) == [0, 2])
check("the renewal's Wed points at ITS OWN box",
      plan[2]["variant_id"] in new_vs, str(plan[2]["variant_id"]))
check("no id from the old pack leaked in",
      not (old_vids & {p["variant_id"] for p in plan.values() if p["variant_id"]}))
c.execute("DELETE FROM subscription_components WHERE subscription_id=?", (NEW,))
c.execute("DELETE FROM subscription_variants WHERE subscription_id=?", (NEW,))
c.execute("DELETE FROM subscription_day_plan WHERE subscription_id=?", (NEW,))
c.execute("DELETE FROM deliveries WHERE subscription_id=?", (NEW,))
c.execute("DELETE FROM subscriptions WHERE id=?", (NEW,))
c.commit()

print("\n== 8. the pages all still render ==")
for path in ["/today", "/subscriptions", f"/subscriptions/{SID}",
             f"/subscriptions/{SID}/edit", "/subscriptions/new",
             f"/subscriptions/new?renew={SID}", f"/subscriptions/{SID}/variant/{vid}",
             "/planner", "/more", "/deliveries", "/revenue", "/subscriptions/review"]:
    check(f"GET {path} -> 200", cl.get(path).status_code == 200)

c.close()
print(f"\n{N - len(FAILS)}/{N} checks passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
