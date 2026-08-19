"""
Tests for who rides which order, what a route measures, and what that owes.

    python test_routes.py

Copies oggainz.db to a throwaway file first and points the app at the copy, so it
can never touch real data. Refuses to run if that fails.

The bug being pinned down here, found on 19 Aug 2026: a rider was hired for the
morning run -- Tambaram, Chrompet, Nanganallur -- and every one of those stops is
12.5-14.8 road km from Perumbakkam. The automatic rule read that as "too far to
ride, book a Porter", so printing Route 1 gave one Perumbakkam stop, 1.3 km and a
Rs 4 allowance for a ride of about 50 km. The orders were still cooked and still
delivered; they were just missing from the printed route, from the map link, and
from the kilometres the allowance is paid on.

So: putting an order on someone's route is the decision to ride it, and Porter
suggestions are off unless switched on. These tests hold that line, and hold the
things it must not break -- a handoff point chosen by hand still wins, and the old
behaviour still works for anyone who turns it back on.
"""

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_routetest.db")
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
import ops                                                       # noqa: E402

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

# The most recent day that actually has lunch orders on it. Working from real
# rows rather than invented ones keeps the distances real.
row = c.execute(
    "SELECT date FROM deliveries WHERE meal_type='Lunch' "
    "AND status IN ('Pending','Delivered') GROUP BY date "
    "HAVING COUNT(*) >= 4 ORDER BY date DESC LIMIT 1").fetchone()
if not row:
    sys.exit("no day with enough lunch orders in this database copy to test against")
DAY, SLOT = row["date"], "Lunch"
core.set_day_override(c, DAY, 1, "test")
print(f"testing against {DAY} {SLOT}\n")


def far_orders():
    """The stops the old rule used to throw away: pinned, and genuinely far."""
    whole = ops.build_route(c, DAY, SLOT, split=False)
    every = whole["stops"] + whole["porter_direct"] + whole["handoffs"]
    return [s for s in every
            if s.get("pinned") and (s.get("km") or 0) >= ops.fnum(c, "porter_min_km")]


def modes_of(route):
    out = {}
    for group in ("stops", "porter_direct", "handoffs"):
        for s in route[group]:
            if s.get("delivery_id"):
                out[s["delivery_id"]] = s["mode"]
    return out


db.set_setting(c, "porter_auto", "0")
FAR = far_orders()
if not FAR:
    sys.exit(f"no far stops on {DAY} to test the Porter rule against")
FAR_ID = FAR[0]["delivery_id"]
print(f"  ({len(FAR)} stop(s) past the Porter threshold; testing with delivery {FAR_ID})\n")


print("== 1. a far order is ridden, not quietly turned into a Porter booking ==")
r = ops.build_route(c, DAY, SLOT, split=False)
check("no order is porter_direct while Porter suggestions are off",
      not r["porter_direct"], [s["name"] for s in r["porter_direct"]])
check("every far order is in the printed route",
      all(s["delivery_id"] in {x["delivery_id"] for x in r["stops"]} for s in FAR))
check("the far stops are in the map link",
      len([s for s in r["stops"] if s.get("lat")]) >= len(FAR))


print("\n== 2. putting an order on a route is the decision to ride it ==")
db.set_setting(c, "porter_auto", "1")            # old behaviour switched back on
# "On no route at all" now takes clearing the direction map too, since a corridor
# supplies a route on its own. That is the point of section 7 -- here it has to be
# out of the way so the Porter fallback is what is actually under test.
db.set_setting(c, "corridor_routes", "{}")
c.execute("UPDATE deliveries SET batch_no=NULL WHERE date=? AND meal_type=?", (DAY, SLOT))
c.execute("UPDATE customer_addresses SET batch_no=NULL")
c.commit()
before = modes_of(ops.build_route(c, DAY, SLOT, split=False))
check("with suggestions ON and no route, the far order goes to Porter",
      before.get(FAR_ID) in ("porter_direct", "porter_handoff"), before.get(FAR_ID))

c.execute("UPDATE deliveries SET batch_no=1 WHERE id=?", (FAR_ID,))
c.commit()
after = modes_of(ops.build_route(c, DAY, SLOT, split=False))
check("the same order on route 1 is ridden", after.get(FAR_ID) == "rider", after.get(FAR_ID))


print("\n== 3. a handoff chosen by hand still wins over both ==")
pp = c.execute("SELECT id FROM porter_points WHERE active=1 AND lat IS NOT NULL "
               "ORDER BY id LIMIT 1").fetchone()
if pp:
    c.execute("UPDATE deliveries SET porter_point_id=? WHERE id=?", (pp["id"], FAR_ID))
    c.commit()
    m = modes_of(ops.build_route(c, DAY, SLOT, split=False))
    check("hand-assigned handoff beats being on a route", m.get(FAR_ID) == "porter_handoff",
          m.get(FAR_ID))
    c.execute("UPDATE deliveries SET porter_point_id=NULL WHERE id=?", (FAR_ID,))
    c.commit()
else:
    print("  (no pinned porter point in this copy -- skipped)")
db.set_setting(c, "porter_auto", "0")


print("\n== 4. printing one route prints that route, and only that route ==")
c.execute("UPDATE deliveries SET batch_no=NULL WHERE date=? AND meal_type=?", (DAY, SLOT))
c.commit()
plan = ops.suggest_batches(c, DAY, SLOT, 2)
ops.ensure_batches(c, DAY, SLOT, 2)
for did, b in plan.items():
    c.execute("UPDATE deliveries SET batch_no=? WHERE id=?", (b, did))
c.commit()

r1 = ops.build_route(c, DAY, SLOT, batch=1)
r2 = ops.build_route(c, DAY, SLOT, batch=2)
ids1 = {s["delivery_id"] for s in r1["stops"] if s.get("delivery_id")}
ids2 = {s["delivery_id"] for s in r2["stops"] if s.get("delivery_id")}
whole_ids = {s["delivery_id"] for s in ops.build_route(c, DAY, SLOT, split=False)["stops"]
             if s.get("delivery_id")}
check("route 1 and route 2 share no orders", not (ids1 & ids2), ids1 & ids2)
check("every order on route 1 really is on route 1",
      all(c.execute("SELECT batch_no FROM deliveries WHERE id=?", (i,)).fetchone()["batch_no"] == 1
          for i in ids1))
check("between them the two routes carry every riding order",
      (ids1 | ids2) == whole_ids, (ids1 | ids2) ^ whole_ids)


print("\n== 5. the ride home is measured and shown ==")
big = r1 if r1["est_km"] >= r2["est_km"] else r2
check("the route reports its return leg", big["return_km"] > 0, big["return_km"])
check("out + back adds up to the total",
      abs(big["outbound_km"] + big["return_km"] - big["est_km"]) <= 0.2,
      f"{big['outbound_km']} + {big['return_km']} vs {big['est_km']}")
check("the allowance is the whole loop at the set rate",
      big["allowance"] == round(big["est_km"] * ops.fnum(c, "petrol_rate")),
      f"{big['allowance']} vs {big['est_km']} x {ops.fnum(c, 'petrol_rate')}")
check("the map link starts and ends at the kitchen",
      big["maps_url"].count(f"{big['kitchen']['lat']},{big['kitchen']['lng']}") == 2)


print("\n== 6. what was ridden is written down, so the week can be paid ==")
ops.record_estimate(c, DAY, SLOT, 1, r1)
ops.record_estimate(c, DAY, SLOT, 2, r2)
saved = c.execute("SELECT * FROM route_batches WHERE date=? AND slot=? AND batch_no=1",
                  (DAY, SLOT)).fetchone()
check("the route's km is stamped on the batch", saved["est_km"] == r1["est_km"],
      saved["est_km"])
check("so is what it owes", saved["allowance"] == r1["allowance"], saved["allowance"])

c.execute("UPDATE route_batches SET rider='Test Rider' WHERE date=? AND slot=?", (DAY, SLOT))
c.commit()
d = date.fromisoformat(DAY)
monday = d - timedelta(days=d.weekday())
week = (monday.isoformat(), (monday + timedelta(days=6)).isoformat())
pay = ops.payout_week(c, *week)
me = next((b for b in pay["riders"] if b["rider"] == "Test Rider"), None)
check("the rider appears in the week's payout", me is not None)
if me:
    check("his km is the sum of his runs",
          abs(me["km"] - round(r1["est_km"] + r2["est_km"], 1)) <= 0.2,
          f"{me['km']} vs {r1['est_km']} + {r2['est_km']}")
    check("his money is his km at the set rate",
          me["amount"] == round(me["km"] * pay["rate"]), me["amount"])

# A figure he corrected at close-out has to beat anything the app measured.
c.execute("UPDATE route_batches SET actual_km=99 WHERE date=? AND slot=? AND batch_no=1",
          (DAY, SLOT))
c.commit()
pay2 = ops.payout_week(c, *week)
line = next(l for l in pay2["lines"] if l["batch_no"] == 1 and l["date"] == DAY)
check("the rider's own km wins over the measured one", line["km"] == 99.0, line["km"])
check("and it is labelled as his figure", line["km_source"] == "rider", line["km_source"])


print("\n== 7. a direction knows its own run, so nobody has to be ticked ==")
# On 19 Aug 2026 only 7 of 32 active addresses had ever been given a route by
# hand. Kumaran, added that week in Nanganallur -- squarely the Tambaram run --
# therefore belonged to no route at all: he was missing from every per-rider
# sheet, and the only sheet that showed him mixed Chrompet with Semmancheri.
db.set_setting(c, "corridor_routes",
               '{"Local": 1, "West": 1, "OMR-N": 2, "OMR-S": 2}')
c.execute("UPDATE deliveries SET batch_no=NULL WHERE date=? AND meal_type=?", (DAY, SLOT))
c.execute("UPDATE customer_addresses SET batch_no=NULL")
c.commit()

whole = ops.build_route(c, DAY, SLOT, split=False)
everyone = whole["stops"] + whole["porter_direct"] + whole["unpinned"]
orders = [s for s in everyone if s.get("delivery_id")]
check("every order has a route without one being set by hand",
      all(s.get("batch_no") for s in orders),
      [s["name"] for s in orders if not s.get("batch_no")])
check("...and it came from the direction it is in",
      all(s.get("batch_from") == "corridor" for s in orders))
for s in orders:
    want = {"West": 1, "Local": 1, "OMR-N": 2, "OMR-S": 2}.get(s.get("corridor"))
    if want:
        check(f"{s['name'][:16]} ({s['corridor']}) rides route {want}",
              s["batch_no"] == want, s["batch_no"])

check("the runs are discoverable without opening the Routes screen",
      len(ops.batches_on_day(c, DAY, SLOT)) >= 1)
check("a run is named after the ground it covers, not a number",
      "Route" not in ops.route_label(c, 1), ops.route_label(c, 1))

# Anything set by hand still wins, at either level.
one = orders[0]
other = 2 if one["batch_no"] == 1 else 1
c.execute("UPDATE deliveries SET batch_no=? WHERE id=?", (other, one["delivery_id"]))
c.commit()
m = {s["delivery_id"]: s for s in
     (lambda r: r["stops"] + r["porter_direct"] + r["unpinned"])(
         ops.build_route(c, DAY, SLOT, split=False))}
check("a route set on the order beats the direction's default",
      m[one["delivery_id"]]["batch_no"] == other)
check("...and says so", m[one["delivery_id"]]["batch_from"] == "order")
c.execute("UPDATE deliveries SET batch_no=NULL WHERE id=?", (one["delivery_id"],))
c.commit()

if one.get("address_id"):
    c.execute("UPDATE customer_addresses SET batch_no=? WHERE id=?",
              (other, one["address_id"]))
    c.commit()
    m = {s["delivery_id"]: s for s in
         (lambda r: r["stops"] + r["porter_direct"] + r["unpinned"])(
             ops.build_route(c, DAY, SLOT, split=False))}
    check("a standing route on the address beats the direction's default",
          m[one["delivery_id"]]["batch_no"] == other)
    check("...and says so", m[one["delivery_id"]]["batch_from"] == "address")
    c.execute("UPDATE customer_addresses SET batch_no=NULL WHERE id=?",
              (one["address_id"],))
    c.commit()

# Switched off, nothing is assumed -- the old behaviour is still reachable.
db.set_setting(c, "corridor_routes", "{}")
none_set = ops.build_route(c, DAY, SLOT, split=False)
check("with no directions mapped, nothing is auto-assigned",
      not any(s.get("batch_no") for s in none_set["stops"] if s.get("delivery_id")))
db.set_setting(c, "corridor_routes",
               '{"Local": 1, "West": 1, "OMR-N": 2, "OMR-S": 2}')


print("\n== 8. the printed sheet carries no per-order clock times ==")
sheet_html = cl.get(f"/sheet/{DAY}/Lunch?batch=1").get_data(as_text=True)
check("no 'Approx.' column on the route table", "Approx." not in sheet_html)
check("no per-stop eta field is rendered", 'data-k="r1.eta"' not in sheet_html)
check("the stop order is still there", 'data-k="r1.n"' in sheet_html)


print("\n== 9. the pages all still render ==")
for path in ["/today", f"/sheet/{DAY}/Lunch", f"/sheet/{DAY}/Lunch?batch=1",
             f"/sheet/{DAY}/Lunch?batch=1&lang=ta", f"/sheet/{DAY}/Dinner",
             f"/routes?d={DAY}&slot=Lunch", f"/routes/payout?d={DAY}",
             "/routes/payout", "/settings", "/more"]:
    res = cl.get(path)
    check(f"GET {path} -> 200", res.status_code == 200, res.status_code)

# The sheet has to carry the things the run needs: the route table, the ride
# home, and some way to open the map even when the QR could not be drawn.
html = cl.get(f"/sheet/{DAY}/Lunch?batch=1").get_data(as_text=True)
check("the printed sheet names the ride home", "Back to the kitchen" in html)
check("the printed sheet shows the trip split", "Full trip" in html)
check("the printed sheet can always reach the map",
      ("data:image/png;base64," in html) or ("google.com/maps/dir" in html))

c.close()
print(f"\n{N - len(FAILS)}/{N} checks passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
