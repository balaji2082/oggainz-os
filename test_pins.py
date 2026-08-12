"""
Tests for map pins: that a location, once set, is actually VISIBLE everywhere.

    python test_pins.py

Copies oggainz.db to a throwaway file first and points the app at the copy, so it
can never touch real data. Refuses to run if that fails.

These cover the three ways a pin used to disappear:

  1. A customer created after the one-time address backfill got no row in
     customer_addresses. Every map screen reads that table, so the customer was
     invisible to all of them — they never appeared on the pin page, could not be
     pinned from it, and never turned up in the pinned list.
  2. The customer form saved the pin onto `customers.lat` only. Nothing on the
     map screens reads that column, so pinning from the customer page looked
     like it worked and changed nothing: still "needs a pin", still no distance.
  3. A trial had nowhere at all to store a location, so it rode every evening as
     "no pin" — no distance, no zone, no share of the km allowance.
"""

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_pintest.db")
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

PIN = "12.893490, 80.204800"          # a real Perumbakkam point, ~minutes away


def in_missing(cid):
    return [r for r in ops.pin_status(c)["missing"] if r["customer_id"] == cid]


def in_pinned(cid):
    return [r for r in ops.pin_status(c)["pinned"] if r["customer_id"] == cid]


print("== 1. every customer is reachable from the pin page ==")
gap = c.execute("SELECT COUNT(*) FROM customers cu WHERE NOT EXISTS "
                "(SELECT 1 FROM customer_addresses a WHERE a.customer_id=cu.id)").fetchone()[0]
check("no customer is left without an address row", gap == 0, f"{gap} without one")

# The bug as Balaji hit it: add a customer, and they are nowhere on the pin page.
r = cl.post("/customers/new", data={"name": "Pin Test One", "phone": "9000000001",
                                    "locality": "Perumbakkam",
                                    "address": "1 Test Street, Perumbakkam"},
            follow_redirects=True)
check("a new customer is created", r.status_code == 200)
NEW1 = c.execute("SELECT id FROM customers WHERE name='Pin Test One'").fetchone()["id"]
check("...and gets an address row immediately",
      c.execute("SELECT COUNT(*) FROM customer_addresses WHERE customer_id=?",
                (NEW1,)).fetchone()[0] == 1)
check("...so they show up as needing a pin", len(in_missing(NEW1)) == 1)
check("...and are not yet in the pinned list", len(in_pinned(NEW1)) == 0)

print("\n== 2. a pin set on the customer form actually lands ==")
r = cl.post(f"/customers/{NEW1}/edit",
            data={"name": "Pin Test One", "phone": "9000000001",
                  "locality": "Perumbakkam", "address": "1 Test Street, Perumbakkam",
                  "pin": PIN}, follow_redirects=True)
check("the edit saves", r.status_code == 200)
row = c.execute("SELECT lat, lng FROM customer_addresses WHERE customer_id=?",
                (NEW1,)).fetchone()
check("the pin reaches the ADDRESS, not just the customer row", row["lat"] is not None,
      str(dict(row)))
check("...so they leave the 'needs a pin' list", len(in_missing(NEW1)) == 0)
got = in_pinned(NEW1)
check("...and appear in the pinned list", len(got) == 1)
if got:
    km, src = ops.road_km_from_kitchen(c, got[0]["lat"], got[0]["lng"])
    check("...with a real distance from the kitchen", km is not None and km >= 0,
          str(km))

print("\n== 3. pinning inline from the pin page ==")
cl.post("/customers/new", data={"name": "Pin Test Two", "phone": "9000000002",
                                "locality": "Perumbakkam", "address": "2 Test Street"},
        follow_redirects=True)
NEW2 = c.execute("SELECT id FROM customers WHERE name='Pin Test Two'").fetchone()["id"]
aid = c.execute("SELECT id FROM customer_addresses WHERE customer_id=?", (NEW2,)).fetchone()["id"]
r = cl.post("/pins/save", data={"address_id": aid, "kind": "address", "pin": PIN})
j = r.get_json()
check("the inline save reports success", j.get("ok") is True, str(j))
check("...and answers with the road distance", isinstance(j.get("km"), (int, float)), str(j))
check("...and a zone", bool(j.get("zone")), str(j))
check("...and the customer is now pinned", len(in_pinned(NEW2)) == 1)

print("\n== 4. a trial can hold a pin too ==")
DAY = date.today().strftime("%Y-%m-%d")
cl.post("/trials/new", data={"name": "Pin Test Trial", "phone": "9000000003",
                             "date": DAY, "delivery_date": DAY, "amount": "249",
                             "meal_type": "Lunch", "delivery_status": "Pending",
                             "address": "3 Test Street, Perumbakkam",
                             "order_text": "Signature trial pack"},
        follow_redirects=True)
t = c.execute("SELECT * FROM trial_orders WHERE name='Pin Test Trial'").fetchone()
check("the trial is created", t is not None)
TID = t["id"]
check("...and starts unpinned, on the work list",
      any(r["pin_id"] == TID for r in ops.pin_status(c)["missing"]))

r = cl.post("/pins/save", data={"address_id": TID, "kind": "trial", "pin": PIN})
j = r.get_json()
check("a trial can be pinned from the pin page", j.get("ok") is True, str(j))
t = c.execute("SELECT lat, lng FROM trial_orders WHERE id=?", (TID,)).fetchone()
check("...and the pin is stored on the trial", t["lat"] is not None)
check("...and it moves into the pinned list",
      any(r["pin_id"] == TID for r in ops.pin_status(c)["pinned"]))

print("\n== 5. the pinned trial reaches the evening route ==")
rows = core.deliveries_for_day(c, DAY)
mine = [r for r in rows if r["name"] == "Pin Test Trial"]
check("the trial is in the day's orders", len(mine) == 1, f"{len(mine)} rows")
if mine:
    check("...and the route query now carries its pin", mine[0]["lat"] is not None)
    klat, klng, _ = ops.kitchen(c)
    stop = ops._stop_from_row(c, mine[0], klat, klng, DAY, {})
    check("...so the route counts it as pinned", stop["pinned"] is True)
    check("...with a distance", stop["km"] is not None, str(stop.get("km")))
    check("...and a zone", bool(stop["zone"]), str(stop.get("zone")))

print("\n== 6. editing a trial never loses its pin ==")
# INSERT OR REPLACE rewrites every column, so an unrelated edit used to wipe it.
cl.post(f"/trials/{TID}/edit", data={"name": "Pin Test Trial", "phone": "9000000003",
                                     "date": DAY, "delivery_date": DAY, "amount": "299",
                                     "meal_type": "Lunch", "delivery_status": "Pending",
                                     "address": "3 Test Street, Perumbakkam",
                                     "order_text": "Signature trial pack"},
        follow_redirects=True)
t = c.execute("SELECT lat, amount FROM trial_orders WHERE id=?", (TID,)).fetchone()
check("the edit saved", t["amount"] == 299)
check("...and the pin survived it", t["lat"] is not None)

print("\n== 7. with two addresses, the pin goes to the DEFAULT one ==")
# A customer with a home and an office: a pin typed on the customer page is the
# place they are delivered to, so it must not land on whichever row is older.
cl.post(f"/customers/{NEW1}/address",
        data={"label": "Office", "address": "99 Office Road, Sholinganallur"},
        follow_redirects=True)
rows = c.execute("SELECT id, label, is_default FROM customer_addresses "
                 "WHERE customer_id=? AND active=1 ORDER BY id", (NEW1,)).fetchall()
check("the customer now has two addresses", len(rows) == 2, str([dict(r) for r in rows]))
home = [r for r in rows if r["is_default"]][0]
office = [r for r in rows if not r["is_default"]][0]
before = c.execute("SELECT lat FROM customer_addresses WHERE id=?", (office["id"],)).fetchone()

NEWPIN = "12.901141, 80.227000"
cl.post(f"/customers/{NEW1}/edit",
        data={"name": "Pin Test One", "phone": "9000000001", "locality": "Perumbakkam",
              "address": "1 Test Street, Perumbakkam", "pin": NEWPIN},
        follow_redirects=True)
h = c.execute("SELECT lat FROM customer_addresses WHERE id=?", (home["id"],)).fetchone()
o = c.execute("SELECT lat FROM customer_addresses WHERE id=?", (office["id"],)).fetchone()
check("the default address takes the new pin", h["lat"] is not None and
      abs(h["lat"] - 12.901141) < 0.0005, str(h["lat"]))
check("...and the other address is left alone", o["lat"] == before["lat"], str(o["lat"]))

print("\n== 8. the pin page renders with all of it ==")
r = cl.get("/pins")
check("GET /pins -> 200", r.status_code == 200)
body = r.get_data(as_text=True)
check("...and lists the pinned trial", "Pin Test Trial" in body)
check("...and the newly pinned customer", "Pin Test Two" in body)

print(f"\n{N - len(FAILS)}/{N} checks passed")
print("ALL GREEN" if not FAILS else "FAILURES: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
